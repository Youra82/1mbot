# tests/test_symbol_settings.py
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.append(os.path.join(PROJECT_ROOT, "src"))

from onembot.utils.symbol_settings import resolve_symbol_settings


def test_no_override_returns_original_settings():
    settings = {"watchlist": ["BTC/USDT:USDT"], "grid": {"spacing_atr_mult": 4.5}}
    resolved = resolve_symbol_settings(settings, "BTC/USDT:USDT")
    assert resolved is settings


def test_override_replaces_top_level_block_for_matching_symbol():
    settings = {
        "grid": {"spacing_atr_mult": 4.5, "levels_per_side": 1},
        "per_symbol_overrides": {
            "ETH/USDT:USDT": {"grid": {"spacing_atr_mult": 3.0, "levels_per_side": 2}},
        },
    }
    resolved_eth = resolve_symbol_settings(settings, "ETH/USDT:USDT")
    assert resolved_eth["grid"] == {"spacing_atr_mult": 3.0, "levels_per_side": 2}

    resolved_btc = resolve_symbol_settings(settings, "BTC/USDT:USDT")
    assert resolved_btc["grid"] == {"spacing_atr_mult": 4.5, "levels_per_side": 1}


def test_override_does_not_mutate_original_settings():
    settings = {
        "grid": {"spacing_atr_mult": 4.5},
        "per_symbol_overrides": {"BTC/USDT:USDT": {"grid": {"spacing_atr_mult": 1.0}}},
    }
    resolve_symbol_settings(settings, "BTC/USDT:USDT")
    assert settings["grid"]["spacing_atr_mult"] == 4.5
