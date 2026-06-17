"""A focused, single-purpose LLM classifier that re-homes unmatched products.

The receipt extraction pass juggles many concerns (line items, totals, dates, store) and
drifts to synonyms the taxonomy does not contain. This classifier does one thing: given a
product, pick exactly one taxonomy category. The narrow prompt makes no reference to
receipts or extraction, and the output is constrained to the allowed taxonomy plus an
explicit "uncategorized" escape, so the model can decline rather than invent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import BaseModel, create_model

from cartlog.normalization import normalize_text

if TYPE_CHECKING:
    from collections.abc import Sequence

# The legal "none of these fit" answer, kept out of the taxonomy itself.
UNCATEGORIZED_CHOICE = "uncategorized"


class CategoryClassifier(Protocol):
    """Categorizes products into a fixed taxonomy, returning canonical_name -> name (or None)."""

    def classify(self, products: Sequence[ProductToClassify]) -> dict[str, str | None]:
        """Categorize each product; None means the classifier declined to place it."""
        ...


@dataclass(frozen=True)
class ProductToClassify:
    """One product to categorize: the normalized name plus signal to disambiguate it."""

    canonical_name: str
    raw_description: str
    # Distinct category strings the extraction pass guessed for this product, for context.
    original_guesses: tuple[str, ...] = field(default_factory=tuple)


def _build_output_model(allowed: Sequence[str]) -> type[BaseModel]:
    """Build a Pydantic result model whose category field is constrained to the taxonomy.

    The category is a Literal over the allowed names plus the uncategorized escape, so the
    structured-output decoder cannot emit an invented category like 'produce'.
    """
    choices = (*allowed, UNCATEGORIZED_CHOICE)
    # Dynamic Literal/model built at runtime from the taxonomy; the type checker cannot follow
    # the dynamic type arguments, so the constructed schema is suppressed here.
    category_type = Literal[choices]  # type: ignore[valid-type, ty:invalid-type-form]
    item_model = create_model(
        "ClassifiedProduct",
        canonical_name=(str, ...),
        category=(category_type, ...),
    )
    return create_model("ClassificationResult", items=(list[item_model], ...))  # type: ignore[ty:invalid-type-form]


class LLMCategoryClassifier:
    """Categorize products into a fixed taxonomy with a narrow, single-purpose LLM call.

    Inject an `anthropic.Anthropic` client so tests can substitute a mock. `allowed_categories`
    constrains the structured output to a fixed taxonomy; it must be non-empty.
    """

    def __init__(self, client: object, model: str, allowed_categories: Sequence[str]) -> None:
        """Store the client/model and precompile the enum-constrained output schema.

        Args:
            client: An anthropic.Anthropic instance (injected so tests can mock it).
            model: The model id to classify with (a cheap model like Haiku is appropriate).
            allowed_categories: The taxonomy names the classifier may choose from.
        """
        if not allowed_categories:
            msg = "LLMCategoryClassifier requires a non-empty allowed_categories list"
            raise ValueError(msg)
        self._client = client
        self._model = model
        self._allowed = list(allowed_categories)
        # Map normalized name -> canonical taxonomy name, to coerce the model's answer back.
        self._allowed_by_norm = {normalize_text(name): name for name in self._allowed}
        self._output_model = _build_output_model(self._allowed)

    def classify(self, products: Sequence[ProductToClassify]) -> dict[str, str | None]:
        """Categorize each product, returning canonical_name -> chosen taxonomy name (or None).

        A None value means the classifier declined ("uncategorized"); the caller should leave
        that product as-is. Products the model omits from its answer are absent from the result.

        Args:
            products: The products to categorize.

        Returns:
            dict mapping each answered product's canonical_name to a taxonomy category name,
            or None when the classifier could not place it.

        Raises:
            ValueError: If the model returned no structured output.
        """
        if not products:
            return {}

        prompt = self._build_prompt(products)
        response = self._client.messages.parse(  # type: ignore[attr-defined, ty:unresolved-attribute]
            model=self._model,
            max_tokens=2048,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
            output_format=self._output_model,
        )
        parsed = response.parsed_output  # type: ignore[attr-defined]
        if parsed is None:
            msg = "Classifier returned no structured output; the response may have been truncated."
            raise ValueError(msg)

        result: dict[str, str | None] = {}
        for entry in parsed.items:
            norm = normalize_text(entry.category)
            # Coerce defensively: anything not a known taxonomy name (incl. the escape) is None.
            result[entry.canonical_name] = self._allowed_by_norm.get(norm)
        return result

    def _build_prompt(self, products: Sequence[ProductToClassify]) -> str:
        """Render the categorization-only prompt for a batch of products."""
        allowed_block = "\n".join(f"- {name}" for name in self._allowed)
        product_lines = []
        for product in products:
            guesses = ", ".join(product.original_guesses) if product.original_guesses else "(none)"
            product_lines.append(
                f'- canonical_name: "{product.canonical_name}" | '
                f'seen on receipt as: "{product.raw_description}" | '
                f"earlier guess: {guesses}"
            )
        products_block = "\n".join(product_lines)
        return (
            "You are categorizing grocery products into a fixed taxonomy. For each product, "
            "choose the single best-fitting category.\n"
            "Rules:\n"
            f'- Choose EXACTLY one name from the allowed list, or "{UNCATEGORIZED_CHOICE}" '
            "only if none truly fits.\n"
            '- There is no "produce" category: classify produce as "fruits" or "vegetables" '
            "based on the item.\n"
            "- Ignore storage descriptions like refrigerated or shelf stable; choose "
            '"frozen" only when the product itself is sold frozen.\n'
            f"Allowed categories:\n{allowed_block}\n\n"
            f"Products to categorize:\n{products_block}\n\n"
            "Return one entry per product with its exact canonical_name and chosen category."
        )
