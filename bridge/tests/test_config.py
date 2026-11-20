from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from flatrig_bridge.__main__ import main
from flatrig_bridge.config import ConfigError, bit_budget, find_settings, load_params, load_settings

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"
SETTINGS_EXAMPLE = CONFIG_DIR / "settings.example.json"
PARAMS_EXAMPLE = CONFIG_DIR / "params.example.json"


def _example(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def workdir(tmp_path: Path) -> Path:
    """example の settings/params を tmp に複製したディレクトリ。"""
    (tmp_path / "settings.json").write_text(json.dumps(_example(SETTINGS_EXAMPLE)), encoding="utf-8")
    (tmp_path / "params.example.json").write_text(json.dumps(_example(PARAMS_EXAMPLE)), encoding="utf-8")
    return tmp_path


def _write(path: Path, data: dict) -> Path:
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _errors(exc: pytest.ExceptionInfo[ConfigError]) -> str:
    return "\n".join(exc.value.errors)


# --- 正常系 -----------------------------------------------------------------


def test_example_files_load():
    settings = load_settings(SETTINGS_EXAMPLE)
    params = load_params(settings.mapping.params_file)
    assert settings.vts.plugin_developer == "FLA_fluffy"
    assert settings.mapping.max_phase == 2
    assert len(params.params) == 14
    assert [f.avatar for f in params.flags] == ["FR_Active", "FR_Enable", "FR_Expr", "FR_AutoBlink"]
    assert [f.type for f in params.flags] == ["bool", "bool", "int", "bool"]


def test_bit_budget_matches_spec():
    params = load_params(PARAMS_EXAMPLE)
    assert bit_budget(params, max_phase=1).bits == 51
    assert bit_budget(params, max_phase=2).bits == 123
    assert bit_budget(params, max_phase=None).bits == 123
    assert bit_budget(params, max_phase=None).int_count == 1


def test_relative_paths_resolve_against_settings_dir(workdir: Path):
    settings = load_settings(workdir / "settings.json")
    assert settings.mapping.params_file == workdir / "params.example.json"
    assert settings.vts.token_file == workdir / ".vts_token.json"


def test_find_settings_from_repo_root_and_bridge(tmp_path: Path):
    cfg = tmp_path / "bridge" / "config"
    cfg.mkdir(parents=True)
    _write(cfg / "settings.example.json", {})
    assert find_settings(tmp_path) == tmp_path / "bridge/config/settings.example.json"
    assert find_settings(tmp_path / "bridge") == cfg / "settings.example.json"
    _write(cfg / "settings.json", {})
    assert find_settings(tmp_path / "bridge") == cfg / "settings.json"


def test_find_settings_missing(tmp_path: Path):
    with pytest.raises(ConfigError):
        find_settings(tmp_path)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "LOCALHOST"])
def test_local_osc_hosts_allowed(workdir: Path, host: str):
    data = _example(SETTINGS_EXAMPLE)
    data["osc"]["host"] = host
    load_settings(_write(workdir / "settings.json", data))


# --- 異常系 -----------------------------------------------------------------


@pytest.mark.parametrize("host", ["192.168.1.10", "example.com", "0.0.0.0", "10.0.0.1"])
def test_external_osc_host_rejected(workdir: Path, host: str):
    data = _example(SETTINGS_EXAMPLE)
    data["osc"]["host"] = host
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(workdir / "settings.json", data))
    assert "G7" in _errors(exc)


@pytest.mark.parametrize(
    ("section", "key", "value", "message"),
    [
        ("vts", "plugin_developer", "ab", "plugin_developer"),
        ("vts", "plugin_name", "x" * 33, "plugin_name"),
        ("vts", "source", "input", "vts.source"),
        ("vts", "port", 70000, "vts.port"),
        ("osc", "port", "9000", "osc.port"),
        ("osc", "address_prefix", "avatar/parameters", "address_prefix"),
        ("osc", "listen_port", 9000, "listen_port"),
        ("mapping", "ema_alpha", 0, "ema_alpha"),
        ("mapping", "ema_alpha", 1.5, "ema_alpha"),
        ("mapping", "max_phase", 0, "max_phase"),
        ("mapping", "quantize", 1, "quantize"),
        ("mapping", "resend_interval_sec", -1, "resend_interval_sec"),
        ("logging", "level", "VERBOSE", "logging.level"),
    ],
)
def test_invalid_settings_rejected(workdir: Path, section: str, key: str, value, message: str):
    data = _example(SETTINGS_EXAMPLE)
    data[section][key] = value
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(workdir / "settings.json", data))
    assert message in _errors(exc)


def test_missing_section_reported(workdir: Path):
    data = _example(SETTINGS_EXAMPLE)
    del data["osc"]
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(workdir / "settings.json", data))
    assert "osc" in _errors(exc)


def test_broken_json(workdir: Path):
    (workdir / "settings.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_settings(workdir / "settings.json")


def test_duplicate_avatar_name_rejected(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    data["params"].append(copy.deepcopy(data["params"][0]) | {"vts": "ParamOther"})
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "avatar 名が重複" in _errors(exc)


def test_duplicate_flag_and_param_name_rejected(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    data["flags"][0]["avatar"] = "FR_AngleX"
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "FR_AngleX" in _errors(exc)


def test_int_flag_bits_checked(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    data["flags"][2]["bits"] = 1
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "同期 int は 8 bit" in _errors(exc)


def test_unknown_flag_type_rejected(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    data["flags"][2]["type"] = "float"
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "bool / int" in _errors(exc)


def test_duplicate_vts_name_rejected(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    data["params"][1]["vts"] = data["params"][0]["vts"]
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "vts 名が重複" in _errors(exc)


def test_bit_budget_overflow_rejected(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    template = data["params"][0]
    for i in range(17):  # 既存 123bit + 17 × 8 = 259bit
        data["params"].append(template | {"avatar": f"FR_Extra{i}", "vts": f"ParamExtra{i}", "phase": 3})
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "259 > 256" in _errors(exc)


@pytest.mark.parametrize(
    ("index", "patch", "message"),
    [
        (0, {"mode": "linear"}, "mode"),
        (0, {"src_min": 30, "src_max": -30}, "src_min < src_max"),
        (0, {"src_min": 1, "src_max": 1}, "src_min < src_max"),
        (0, {"bits": 16}, "bits"),
        (0, {"phase": 0}, "phase"),
        (0, {"avatar": "AngleX"}, "接頭辞"),
    ],
)
def test_invalid_param_rejected(tmp_path: Path, index: int, patch: dict, message: str):
    data = _example(PARAMS_EXAMPLE)
    data["params"][index] |= patch
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert message in _errors(exc)


# --- CLI --------------------------------------------------------------------


def test_cli_check_config_ok(capsys):
    assert main(["check-config", "--settings", str(SETTINGS_EXAMPLE)]) == 0
    out = capsys.readouterr().out
    assert "Phase 1 時点" in out and " 51 bit" in out
    assert "123 bit" in out and "int 1 × 8" in out
    assert "検証: OK" in out


def test_cli_check_config_error(workdir: Path, capsys):
    data = _example(SETTINGS_EXAMPLE)
    data["osc"]["host"] = "192.168.0.5"
    path = _write(workdir / "settings.json", data)
    assert main(["check-config", "--settings", str(path)]) == 1
    assert "G7" in capsys.readouterr().out


def test_cli_does_not_print_token(workdir: Path, capsys):
    (workdir / ".vts_token.json").write_text('{"token": "SECRET-TOKEN-VALUE"}', encoding="utf-8")
    assert main(["check-config", "--settings", str(workdir / "settings.json")]) == 0
    assert "SECRET-TOKEN-VALUE" not in capsys.readouterr().out


# --- vts_aliases(SPEC §3.1, U15) ---------------------------------------------


def test_aliases_loaded():
    params = load_params(PARAMS_EXAMPLE)
    mouth = next(p for p in params.params if p.avatar == "FR_MouthOpenY")
    assert mouth.vts_names == ("ParamMouthOpenY", "ParamMouthOpen")
    angle = next(p for p in params.params if p.avatar == "FR_AngleX")
    assert angle.vts_names == ("ParamAngleX",)


def test_find_prefers_primary_name():
    mouth = next(p for p in load_params(PARAMS_EXAMPLE).params if p.avatar == "FR_MouthOpenY")
    assert mouth.find({"ParamMouthOpen": 1, "ParamMouthOpenY": 2}) == ("ParamMouthOpenY", 2)
    assert mouth.find({"ParamMouthOpen": 1}) == ("ParamMouthOpen", 1)
    assert mouth.find({}) is None


@pytest.mark.parametrize("aliases", ["ParamX", [1], [""], {"a": 1}])
def test_invalid_aliases_rejected(tmp_path: Path, aliases):
    data = _example(PARAMS_EXAMPLE)
    data["params"][0]["vts_aliases"] = aliases
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "vts_aliases" in _errors(exc)


def test_alias_colliding_with_other_param_rejected(tmp_path: Path):
    data = _example(PARAMS_EXAMPLE)
    data["params"][0]["vts_aliases"] = ["ParamAngleY"]
    with pytest.raises(ConfigError) as exc:
        load_params(_write(tmp_path / "params.json", data))
    assert "ParamAngleY" in _errors(exc)


# --- hands(SPEC §4.9) -------------------------------------------------------


def test_hands_section_optional(workdir: Path):
    s = load_settings(workdir / "settings.json")
    assert s.hands.enabled is True and s.hands.swap is False


def test_hands_section_read(workdir: Path):
    data = _example(SETTINGS_EXAMPLE)
    data["hands"] = {"enabled": False, "swap": True, "hold_sec": 1}
    s = load_settings(_write(workdir / "settings.json", data))
    assert (s.hands.enabled, s.hands.swap, s.hands.hold_sec) == (False, True, 1.0)


def test_hands_section_invalid(workdir: Path):
    data = _example(SETTINGS_EXAMPLE)
    data["hands"] = {"enabled": "yes", "hold_sec": -1}
    with pytest.raises(ConfigError) as exc:
        load_settings(_write(workdir / "settings.json", data))
    assert "hands.enabled" in _errors(exc) and "hands.hold_sec" in _errors(exc)
