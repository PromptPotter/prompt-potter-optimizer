"""Tests for grid search functions in _campaign_lib.py."""
import sys
from pathlib import Path

import pytest

# Ensure project root is importable
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
# Ensure notebooks dir is importable for _campaign_lib
_NOTEBOOKS_DIR = _PROJECT_ROOT / "notebooks"
if str(_NOTEBOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_NOTEBOOKS_DIR))

from _campaign_lib import (
    DEFAULT_GRID_AXES,
    GRID_SEARCHABLE_FIELDS,
    REQUIRED_TEMPLATE_VARS,
    build_grid_combinations,
    validate_grid_config,
)
from api.models.prompt_state import PromptState


@pytest.fixture
def baseline():
    """A minimal baseline PromptState for grid tests."""
    return PromptState(
        instruction="Rank candidates for {{core_concept}} given {{entity_profile_json}} and {{matches}}.",
        changes_description="test baseline",
    )


# ---------------------------------------------------------------------------
# DEFAULT_GRID_AXES well-formedness
# ---------------------------------------------------------------------------


class TestDefaultGridAxes:
    def test_all_keys_are_searchable(self):
        """Every key in DEFAULT_GRID_AXES must be in GRID_SEARCHABLE_FIELDS."""
        for key in DEFAULT_GRID_AXES:
            assert key in GRID_SEARCHABLE_FIELDS, f"{key} not in GRID_SEARCHABLE_FIELDS"

    def test_each_axis_has_at_least_two_values(self):
        """Each axis must have at least 2 variants (including empty)."""
        for key, values in DEFAULT_GRID_AXES.items():
            assert len(values) >= 2, f"{key} has only {len(values)} values"

    def test_each_axis_contains_empty_string(self):
        """Each default axis should include an empty-string variant."""
        for key, values in DEFAULT_GRID_AXES.items():
            assert "" in values, f"{key} is missing the empty-string variant"

    def test_values_are_strings(self):
        """All axis values must be strings."""
        for key, values in DEFAULT_GRID_AXES.items():
            for v in values:
                assert isinstance(v, str), f"{key} contains non-string: {type(v)}"


# ---------------------------------------------------------------------------
# validate_grid_config
# ---------------------------------------------------------------------------


class TestValidateGridConfig:
    def test_valid_config(self, baseline):
        config = {"persona": ["", "Expert"], "thinking_style": ["", "Step by step"]}
        meta = validate_grid_config(config, baseline)
        assert meta["total"] == 4
        assert meta["axis_names"] == ["persona", "thinking_style"]
        assert meta["is_subsampled"] is False

    def test_rejects_invalid_field(self, baseline):
        config = {"bogus_field": ["a", "b"]}
        with pytest.raises(ValueError, match="Invalid grid axis fields"):
            validate_grid_config(config, baseline)

    def test_rejects_instruction_missing_template_vars(self, baseline):
        config = {"instruction": ["", "Just rank them, no template vars needed."]}
        with pytest.raises(ValueError, match="missing template variables"):
            validate_grid_config(config, baseline)

    def test_accepts_instruction_with_all_template_vars(self, baseline):
        valid_instruction = (
            "Given {{core_concept}} and {{entity_profile_json}}, "
            "rank {{matches}} by relevance."
        )
        config = {"instruction": ["", valid_instruction]}
        meta = validate_grid_config(config, baseline)
        assert meta["total"] == 2

    def test_empty_instruction_variant_allowed(self, baseline):
        """Empty string instruction variant should not be validated for template vars."""
        config = {"instruction": [""]}
        meta = validate_grid_config(config, baseline)
        assert meta["total"] == 1

    def test_default_axes_validate(self, baseline):
        """The shipped DEFAULT_GRID_AXES must pass validation."""
        meta = validate_grid_config(dict(DEFAULT_GRID_AXES), baseline)
        expected = 1
        for values in DEFAULT_GRID_AXES.values():
            expected *= len(values)
        assert meta["total"] == expected


# ---------------------------------------------------------------------------
# build_grid_combinations
# ---------------------------------------------------------------------------


class TestBuildGridCombinations:
    def test_correct_count(self, baseline):
        config = {"persona": ["", "A", "B"], "thinking_style": ["", "X"]}
        combos, lookup = build_grid_combinations(config, baseline)
        assert len(combos) == 6  # 3 x 2
        assert len(lookup) == 6

    def test_subsampling(self, baseline):
        config = {"persona": ["", "A", "B"], "thinking_style": ["", "X"]}
        combos, lookup = build_grid_combinations(config, baseline, max_combinations=3)
        assert len(combos) == 3
        assert len(lookup) == 3

    def test_subsampling_is_reproducible(self, baseline):
        config = {"persona": ["", "A", "B"], "thinking_style": ["", "X"]}
        combos1, _ = build_grid_combinations(config, baseline, max_combinations=3, seed=42)
        combos2, _ = build_grid_combinations(config, baseline, max_combinations=3, seed=42)
        assert [c[0] for c in combos1] == [c[0] for c in combos2]

    def test_each_combo_is_a_prompt_state(self, baseline):
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        for coord, ps_id in combos:
            ps = lookup[ps_id]
            assert isinstance(ps, PromptState)
            assert ps.parent_id == baseline.id

    def test_non_empty_values_override_baseline(self, baseline):
        config = {"persona": ["", "Expert"]}
        combos, lookup = build_grid_combinations(config, baseline)
        # Find the combo where persona index is 1
        for coord, ps_id in combos:
            if coord["persona"] == 1:
                assert lookup[ps_id].persona == "Expert"
            elif coord["persona"] == 0:
                # Empty string means no override — baseline persona is ""
                assert lookup[ps_id].persona == baseline.persona

    def test_changes_description_format(self, baseline):
        config = {"persona": ["", "A"], "task_intent": ["", "B"]}
        combos, lookup = build_grid_combinations(config, baseline)
        for _, ps_id in combos:
            ps = lookup[ps_id]
            assert ps.changes_description.startswith("grid[")
            assert ps.changes_description.endswith("]")

    def test_single_axis_grid(self, baseline):
        config = {"answer_format": ["", "JSON", "Markdown"]}
        combos, lookup = build_grid_combinations(config, baseline)
        assert len(combos) == 3

    def test_default_axes_full_product(self, baseline):
        """Full default grid produces the expected cartesian product size."""
        combos, lookup = build_grid_combinations(dict(DEFAULT_GRID_AXES), baseline)
        expected = 1
        for values in DEFAULT_GRID_AXES.values():
            expected *= len(values)
        assert len(combos) == expected
