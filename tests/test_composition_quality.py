import base64
import io
import json
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from webapp import composition_quality as quality
from webapp import openai_provider


def _check(matches=True, confidence=0.96, evidence="Compared visible edges and objects."):
    return {"matches": matches, "confidence": confidence, "evidence": evidence}


def _row(asset_id="table-1", count=1):
    return {
        "asset_id": asset_id, "reference_kind": "single_object",
        "reference_object_count": 1, "observed_instance_count": count,
        "observed_object_count": count, "count_confidence": 0.96,
        "identity_match": True, "identity_confidence": 0.96,
        "set_complete": True, "evidence": "One oak table at the left; distinctive crossed legs match.",
        "placement": _check(),
    }


def _answer(rows=None):
    return {
        "inventory": [_row()] if rows is None else rows,
        "finishes": [],
        "checks": {name: _check() for name in ("exclusive_inventory", "fixed_structure", "source_objects")},
    }


@pytest.fixture
def inspection(monkeypatch, tmp_path):
    """Mock the SDK response boundary; the global network guard forbids paid calls."""
    source = tmp_path / "source.png"
    after = tmp_path / "after.png"
    asset = tmp_path / "asset.png"
    Image.new("RGB", (1600, 900), "navy").save(source)
    Image.new("RGB", (1600, 900), "green").save(after)
    Image.new("RGBA", (1200, 500), (0, 0, 0, 0)).save(asset)
    settings = openai_provider.OpenAISettings(api_key="test-super-secret")
    monkeypatch.setattr(openai_provider.OpenAISettings, "from_env", lambda **kwargs: settings)
    state = {"answer": _answer(), "calls": [], "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}}

    def fake_create(**kwargs):
        state["calls"].append(kwargs)
        if state.get("error"):
            raise state["error"]
        if state.get("http_status", 200) != 200:
            raise RuntimeError("test-super-secret https://private.invalid/token")
        content = state.get("content", json.dumps(state["answer"]))
        parts = [SimpleNamespace(type="output_text", text=content)]
        if state.get("refusal"):
            parts.append(SimpleNamespace(type="refusal", refusal="test-super-secret"))
        return SimpleNamespace(status=state.get("status", "completed"), error=state.get("response_error"),
                               incomplete_details=state.get("incomplete_details"), usage=state["usage"],
                               model=settings.vision_model,
                               output=[SimpleNamespace(type="message", status=state.get("message_status", "completed"), content=parts)])

    def fake_client(**kwargs):
        state["client_options"] = kwargs
        return SimpleNamespace(responses=SimpleNamespace(create=fake_create))

    monkeypatch.setattr(openai_provider, "OpenAI", fake_client)
    state.update(source=source, after=after, asset=asset, settings=settings)

    def run(assets=None, snapshot=None):
        return quality.verify_composition(source, after, assets if assets is not None else [
            {"asset_id": "table-1", "asset_name": "Oak table", "category": "table", "path": asset},
        ], snapshot or {})

    state["run"] = run
    return state


def test_success_requires_explicit_confident_visual_checks(inspection):
    result = inspection["run"]()
    assert result["status"] == "passed"
    assert result["issues"] == []
    assert result["repair_instruction"] == ""
    assert result["inventory"][0]["count_status"] == "passed"
    assert result["inventory"][0]["placement"]["status"] == "not_applicable"
    assert result["checks"]["source_objects"]["status"] == "not_applicable"
    assert result["method"] == "openai_vision"
    assert result["provider"] == "openai"
    assert result["model"] == openai_provider.DEFAULT_VISION_MODEL
    assert result["usage"]["total_tokens"] == 300
    assert result["usage_available"] is True


def test_known_two_references_three_tables_detects_duplicate_identity(inspection):
    second = _row("table-2", count=2)
    second["evidence"] = "The second reference round table appears twice, center and right."
    inspection["answer"] = _answer([_row(), second])
    assets = [{"asset_id": name, "path": inspection["asset"]} for name in ("table-1", "table-2")]
    result = inspection["run"](assets=assets)
    assert result["status"] == "failed"
    issue = next(item for item in result["issues"] if item["code"] == "asset_duplicated")
    assert issue["asset_id"] == "table-2"
    assert (issue["expected_count"], issue["observed_count"]) == (1, 2)
    assert "table-2" in result["repair_instruction"]
    assert len(inspection["calls"]) == 1


@pytest.mark.parametrize("content", ["not JSON", "```json\n{}\n```", "[]", "{}", '{"inventory":[]}'])
def test_invalid_or_partial_answer_is_unverified_never_passed(inspection, content):
    inspection["content"] = content
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["repair_instruction"] == ""
    assert result["provider_invoked"] is True
    assert result["usage"]["total_tokens"] == 300
    assert result["inventory"][0]["observed_count"] is None


def test_duplicate_json_keys_are_rejected_not_last_value_wins(inspection):
    inspection["content"] = json.dumps(inspection["answer"]).replace('"observed_instance_count": 1', '"observed_instance_count": 2, "observed_instance_count": 1')
    assert inspection["run"]()["status"] == "unverified"


@pytest.mark.parametrize("field,value", [("observed_instance_count", True), ("observed_instance_count", "2"),
                                        ("count_confidence", 1.5), ("identity_match", "true")])
def test_schema_does_not_coerce_invalid_counts_or_booleans(inspection, field, value):
    inspection["answer"]["inventory"][0][field] = value
    assert inspection["run"]()["status"] == "unverified"


def test_object_set_is_one_asset_not_multiple_duplicate_objects(inspection):
    row = inspection["answer"]["inventory"][0]
    row.update(reference_kind="object_set", reference_object_count=3, observed_object_count=3)
    row["evidence"] = "One nesting table set with its three original members in the foreground."
    result = inspection["run"]()
    assert result["status"] == "passed"
    assert result["inventory"][0]["observed_count"] == 1
    assert result["inventory"][0]["observed_object_count"] == 3


def test_live_two_single_objects_with_null_set_completeness_pass(inspection):
    """Regression for live job 43be838f: completeness is N/A, not missing proof."""
    rows = [_row("table-1"), _row("table-2")]
    for row in rows:
        row.update(count_confidence=1.0, identity_confidence=1.0, set_complete=None)
    inspection["answer"] = _answer(rows)
    for name in ("exclusive_inventory", "fixed_structure"):
        inspection["answer"]["checks"][name] = _check(True, 0.95)
    assets = [{"asset_id": row["asset_id"], "path": inspection["asset"]} for row in rows]
    result = inspection["run"](assets=assets)
    assert result["status"] == "passed"
    assert [row["count_status"] for row in result["inventory"]] == ["passed", "passed"]
    assert result["issues"] == []
    assert result["repair_instruction"] == ""


@pytest.mark.parametrize("set_complete", [None, False, True])
def test_single_object_contradictory_counts_are_unverified_regardless_of_set_field(inspection, set_complete):
    inspection["answer"]["inventory"][0].update(observed_object_count=2, set_complete=set_complete)
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["inventory"][0]["count_status"] == "unverified"
    assert result["repair_instruction"] == ""


def test_object_set_still_requires_completeness(inspection):
    inspection["answer"]["inventory"][0].update(
        reference_kind="object_set", reference_object_count=3, observed_object_count=3, set_complete=None,
    )
    assert inspection["run"]()["status"] == "unverified"


def test_missing_set_member_is_failure_even_with_one_overall_group(inspection):
    row = inspection["answer"]["inventory"][0]
    row.update(reference_kind="object_set", reference_object_count=3, observed_object_count=2, set_complete=False)
    result = inspection["run"]()
    assert result["status"] == "failed"
    assert result["issues"][0]["code"] == "asset_members_changed"


def test_conflicting_set_counts_do_not_falsely_fail_or_pass(inspection):
    row = inspection["answer"]["inventory"][0]
    row.update(reference_kind="object_set", reference_object_count=3, observed_object_count=3, observed_instance_count=2)
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["repair_instruction"] == ""


def test_ambiguous_reference_count_cannot_pass(inspection):
    row = inspection["answer"]["inventory"][0]
    row.update(reference_kind="uncertain", reference_object_count=None, observed_object_count=None)
    assert inspection["run"]()["status"] == "unverified"


def test_low_confidence_duplicate_does_not_trigger_automatic_repair(inspection):
    inspection["answer"]["inventory"] = [_row(count=2)]
    inspection["answer"]["inventory"][0]["count_confidence"] = 0.6
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["repair_instruction"] == ""


def test_missing_asset_does_not_create_spurious_identity_failure(inspection):
    row = _row(count=0)
    row.update(identity_match=False, set_complete=False)
    inspection["answer"]["inventory"] = [row]
    result = inspection["run"]()
    assert result["status"] == "failed"
    assert [issue["code"] for issue in result["issues"]] == ["asset_missing"]
    assert result["inventory"][0]["identity_status"] == "not_applicable"


def test_required_check_needs_evidence_even_with_high_confidence(inspection):
    inspection["answer"]["checks"]["fixed_structure"]["evidence"] = ""
    assert inspection["run"]()["status"] == "unverified"


def test_finish_only_preserves_source_objects_and_fixed_structure(inspection):
    inspection["answer"] = _answer([])
    inspection["answer"]["checks"]["exclusive_inventory"] = _check(None, 0, "")
    assert inspection["run"](assets=[])["status"] == "passed"
    inspection["answer"]["checks"]["source_objects"] = _check(False, evidence="BEFORE sofa is absent in AFTER.")
    result = inspection["run"](assets=[])
    assert result["status"] == "failed"
    assert result["issues"][0]["code"] == "source_objects_changed"
    assert "Restore all BEFORE furniture" in result["repair_instruction"]


@pytest.mark.parametrize("name,code", [("exclusive_inventory", "unselected_objects"), ("fixed_structure", "fixed_structure_changed")])
def test_unselected_objects_and_structural_changes_are_real_failures(inspection, name, code):
    inspection["answer"]["checks"][name] = _check(False)
    result = inspection["run"]()
    assert result["status"] == "failed"
    assert result["issues"][0]["code"] == code


def test_manual_placement_is_checked_only_when_requested(inspection):
    inspection["answer"]["inventory"][0]["placement"] = _check(False, evidence="Table is at right, but assigned box is left.")
    assert inspection["run"]()["status"] == "passed"
    snapshot = {"placements": [{"asset_id": "table-1", "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}]}
    result = inspection["run"](snapshot=snapshot)
    assert result["status"] == "failed"
    assert result["issues"][0]["code"] == "manual_placement_changed"
    assert "normalized full-frame box" in result["repair_instruction"]


def test_uncertain_manual_placement_never_passes(inspection):
    inspection["answer"]["inventory"][0]["placement"] = _check(None, 0.3, "Heavy occlusion.")
    snapshot = {"placements": [{"asset_id": "table-1", "x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4}]}
    assert inspection["run"](snapshot=snapshot)["status"] == "unverified"


def test_confident_failure_repairs_only_known_constraints(inspection):
    inspection["answer"]["inventory"] = [_row(count=2)]
    inspection["answer"]["checks"]["fixed_structure"] = _check(None, 0.4, "Edges are obscured.")
    result = inspection["run"]()
    assert result["status"] == "failed"
    assert "exactly one complete instance" in result["repair_instruction"]
    assert "Restore BEFORE camera" not in result["repair_instruction"]
    assert any(issue["code"] == "visual_uncertain" for issue in result["issues"])


def test_http_failure_is_safe_unverified_and_never_leaks_provider_error(inspection):
    inspection["http_status"] = 401
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["provider_invoked"] is True
    assert result["usage_available"] is False
    assert "secret" not in json.dumps(result)
    assert "private.invalid" not in json.dumps(result)


def test_connection_error_does_not_crash_job_or_leak_details(inspection):
    inspection["error"] = httpx.ConnectError("test-super-secret")
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert "test-super-secret" not in repr(result)


def test_provider_evidence_and_usage_cannot_expose_api_key(inspection):
    inspection["answer"]["inventory"][0]["evidence"] = "Visible table. test-super-secret"
    inspection["usage"] = {"total_tokens": 300, "api_key": "test-super-secret", "cost_usd": 99,
                           "completion_tokens_details": {"reasoning_tokens": 2, "debug": "test-super-secret"}}
    result = inspection["run"]()
    assert "test-super-secret" not in json.dumps(result)
    assert result["usage"] == {"total_tokens": 300, "completion_tokens_details": {"reasoning_tokens": 2}}
    assert "cost_usd" not in result


def test_no_usage_does_not_fabricate_billing(inspection):
    inspection["usage"] = None
    result = inspection["run"]()
    assert result["status"] == "passed"
    assert result["usage"] == {}
    assert result["usage_available"] is False


def test_no_provider_config_does_not_make_a_request(inspection, monkeypatch):
    monkeypatch.setattr(openai_provider.OpenAISettings, "from_env", lambda **kwargs: openai_provider.OpenAISettings(api_key=""))
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["issues"][0]["code"] == "vision_unavailable"
    assert result["provider_invoked"] is False
    assert inspection["calls"] == []


def test_input_limit_is_enforced_before_any_paid_request(inspection):
    assets = [{"asset_id": f"asset-{index}", "path": inspection["asset"]} for index in range(9)]
    result = inspection["run"](assets=assets)
    assert result["status"] == "unverified"
    assert result["provider_invoked"] is False
    assert inspection["calls"] == []


def test_duplicate_input_ids_are_not_silently_merged(inspection):
    assets = [{"asset_id": "same", "path": inspection["asset"]}] * 2
    assert inspection["run"](assets=assets)["status"] == "unverified"
    assert inspection["calls"] == []


def test_response_inventory_ids_must_match_all_selected_ids(inspection):
    inspection["answer"]["inventory"][0]["asset_id"] = "unknown"
    assert inspection["run"]()["status"] == "unverified"


def test_truncated_model_output_is_not_accepted_even_when_json_parses(inspection):
    inspection["status"] = "incomplete"
    assert inspection["run"]()["status"] == "unverified"


def test_images_are_bounded_temporary_and_originals_are_untouched(inspection, monkeypatch):
    originals = {key: inspection[key].read_bytes() for key in ("source", "after", "asset")}
    generated = []
    original_prepare = quality._inspection_image

    def prepare(source, output):
        generated.append(output)
        original_prepare(source, output)

    monkeypatch.setattr(quality, "_inspection_image", prepare)
    result = inspection["run"]()
    assert result["status"] == "passed"
    assert len(generated) == 3
    assert all(not path.exists() and not path.parent.exists() for path in generated)
    assert all(inspection[key].read_bytes() == value for key, value in originals.items())
    assert inspection["client_options"]["timeout"] == 90.0
    assert inspection["client_options"]["max_retries"] == 0
    assert inspection["client_options"]["base_url"] == "https://api.openai.com/v1"
    payload = inspection["calls"][0]
    assert payload["text"]["format"] == {"type": "json_schema", "name": "composition_inspection", "schema": quality._ANSWER_SCHEMA, "strict": True}
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 6000
    images = [part["image_url"] for part in payload["input"][0]["content"] if part["type"] == "input_image"]
    assert len(images) == 3
    for url in images:
        assert url.startswith("data:image/jpeg;base64,")
        with Image.open(io.BytesIO(base64.b64decode(url.split(",", 1)[1]))) as image:
            assert max(image.size) <= quality.MAX_IMAGE_SIDE
    with Image.open(io.BytesIO(base64.b64decode(images[-1].split(",", 1)[1]))) as image:
        assert image.getpixel((0, 0)) == (255, 255, 255)
    prompt = json.dumps(payload)
    assert "test-super-secret" not in prompt
    assert str(inspection["source"]) not in prompt


def test_missing_image_is_unverified_without_request(inspection):
    result = inspection["run"](assets=[{"asset_id": "table-1", "path": inspection["asset"].with_name("missing.png")}])
    assert result["status"] == "unverified"
    assert result["provider_invoked"] is False
    assert inspection["calls"] == []


def _finish_snapshot(inspection, target="accent_wall"):
    return {"finishes": [{"id": "wallpaper-1", "name": "Warm linen", "category": "wallpaper",
                          "target": target, "source_path": str(inspection["asset"])}]}


def _finish_answer(inspection, material=None, target=None):
    inspection["answer"]["finishes"] = [{"finish_id": "wallpaper-1", "material": material or _check(), "target": target or _check()}]


def test_selected_finish_uses_actual_swatch_and_explicit_surface(inspection):
    _finish_answer(inspection)
    result = inspection["run"](snapshot=_finish_snapshot(inspection))
    assert result["status"] == "passed"
    assert result["checks"]["finish_targets"]["status"] == "passed"
    assert result["finishes"][0]["requested_target"] == "accent_wall"
    content = inspection["calls"][0]["input"][0]["content"]
    assert len([part for part in content if part["type"] == "input_image"]) == 4
    assert content[-2] == {"type": "input_text", "text": "FINISH wallpaper-1"}
    assert "accent_wall" in content[0]["text"]
    assert str(inspection["asset"]) not in content[0]["text"]


@pytest.mark.parametrize("name,code", [("material", "finish_material_changed"), ("target", "finish_target_changed")])
def test_clear_material_or_target_failure_blocks_pass_and_guides_repair(inspection, name, code):
    _finish_answer(inspection, **{name: _check(False, evidence="Requested linen on one wall; AFTER has marble across two walls.")})
    result = inspection["run"](snapshot=_finish_snapshot(inspection))
    assert result["status"] == "failed"
    assert result["checks"]["finish_targets"]["status"] == "failed"
    assert result["issues"][0]["code"] == code
    assert result["issues"][0]["finish_id"] == "wallpaper-1"
    assert "wallpaper-1" in result["repair_instruction"]


@pytest.mark.parametrize("name", ["material", "target"])
def test_uncertain_finish_material_or_boundary_is_unverified(inspection, name):
    _finish_answer(inspection, **{name: _check(None, 0.4, "Target is occluded.")})
    result = inspection["run"](snapshot=_finish_snapshot(inspection))
    assert result["status"] == "unverified"
    assert result["checks"]["finish_targets"]["status"] == "unverified"
    assert result["repair_instruction"] == ""


def test_finish_only_pass_requires_both_source_preservation_and_finish_checks(inspection):
    inspection["answer"] = _answer([])
    _finish_answer(inspection)
    result = inspection["run"](assets=[], snapshot=_finish_snapshot(inspection))
    assert result["status"] == "passed"
    assert result["checks"]["source_objects"]["status"] == "passed"
    assert result["checks"]["finish_targets"]["status"] == "passed"


def test_missing_finish_in_provider_answer_is_unverified(inspection):
    assert inspection["run"](snapshot=_finish_snapshot(inspection))["status"] == "unverified"


def test_selected_finish_without_reference_never_makes_paid_call(inspection):
    snapshot = _finish_snapshot(inspection)
    snapshot["finishes"][0]["source_path"] = str(inspection["asset"].with_name("missing-swatch.png"))
    result = inspection["run"](snapshot=snapshot)
    assert result["status"] == "unverified"
    assert result["provider_invoked"] is False
    assert inspection["calls"] == []


def test_finish_reference_limit_is_bounded(inspection):
    snapshot = {"finishes": [{"id": f"finish-{index}", "category": "wallpaper", "target": "auto", "source_path": str(inspection["asset"])} for index in range(5)]}
    assert inspection["run"](snapshot=snapshot)["status"] == "unverified"
    assert inspection["calls"] == []


@pytest.mark.parametrize("change", [
    {"refusal": True}, {"status": "incomplete"}, {"status": "failed"},
    {"message_status": "incomplete"}, {"incomplete_details": {"reason": "max_output_tokens"}},
    {"response_error": {"message": "test-super-secret"}},
])
def test_refusals_and_incomplete_responses_are_unverified_with_usage_preserved(inspection, change):
    inspection.update(change)
    result = inspection["run"]()
    assert result["status"] == "unverified"
    assert result["provider_invoked"] is True
    assert result["usage"]["total_tokens"] == 300
    assert result["repair_instruction"] == ""
    assert "test-super-secret" not in repr(result)
    assert len(inspection["calls"]) == 1


def test_strict_schema_is_real_nested_required_json_schema():
    def walk(node):
        assert isinstance(node, dict)
        if node.get("type") == "object":
            assert node["additionalProperties"] is False
            assert set(node["required"]) == set(node["properties"])
            for child in node["properties"].values():
                walk(child)
        elif node.get("type") == "array":
            walk(node["items"])
        else:
            assert "type" in node
    assert quality._ANSWER_SCHEMA["type"] == "object"
    walk(quality._ANSWER_SCHEMA)
    row = quality._ANSWER_SCHEMA["properties"]["inventory"]["items"]["properties"]
    assert row["set_complete"]["type"] == ["boolean", "null"]
    assert row["observed_instance_count"]["type"] == ["integer", "null"]


@pytest.mark.parametrize("location", ["root", "inventory", "check"])
def test_extra_fields_from_malformed_transport_cannot_pass(inspection, location):
    target = (inspection["answer"] if location == "root" else inspection["answer"]["inventory"][0]
              if location == "inventory" else inspection["answer"]["checks"]["fixed_structure"])
    target["unexpected"] = "not part of strict schema"
    assert inspection["run"]()["status"] == "unverified"


def test_real_responses_sdk_serializes_strict_schema_and_image_inputs(inspection, monkeypatch):
    from openai import OpenAI as RealOpenAI
    calls = []
    def transport(request):
        calls.append((str(request.url), json.loads(request.read())))
        return httpx.Response(200, json={
            "id": "resp_mock", "object": "response", "created_at": 1, "status": "completed",
            "model": "gpt-5-mini", "error": None, "incomplete_details": None,
            "output": [{"id": "msg_mock", "type": "message", "status": "completed", "role": "assistant",
                        "content": [{"type": "output_text", "text": json.dumps(inspection["answer"]), "annotations": []}]}],
            "usage": {"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        })
    monkeypatch.setattr(openai_provider, "OpenAI", lambda **kw: RealOpenAI(
        **kw, http_client=httpx.Client(transport=httpx.MockTransport(transport))))
    result = inspection["run"]()
    assert result["status"] == "passed"
    assert len(calls) == 1
    url, payload = calls[0]
    assert url == "https://api.openai.com/v1/responses"
    assert payload["store"] is False
    assert payload["text"]["format"]["strict"] is True
    assert payload["model"] == "gpt-5-mini"
    assert len([part for part in payload["input"][0]["content"] if part["type"] == "input_image"]) == 3
