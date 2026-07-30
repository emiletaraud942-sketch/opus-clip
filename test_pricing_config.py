"""Tests unitaires de la formule de quota (critère d'acceptation #8).

Lancer : python -m pytest test_pricing_config.py   (ou : python test_pricing_config.py)
"""

import pricing_config as pc


# Table de référence du cahier des charges : prix → quota affiché (minutes).
REFERENCE_TABLE = {
    9.99: 75,
    19.0: 160,
    29.0: 250,
    49.0: 450,
    99.0: 900,
}


def test_quota_matches_reference_table():
    for price, expected in REFERENCE_TABLE.items():
        got = pc.quota_from_price(price)
        assert got == expected, f"prix {price}€ : attendu {expected}, obtenu {got}"


def test_minutes_seuil_is_break_even():
    # À minutes_seuil, revenu net = coût de calcul (marge = TARGET_MARGIN).
    price = 19.0
    minutes = pc._minutes_seuil(price)
    cout_calcul = minutes * pc.COST_PER_SOURCE_MINUTE_EUR
    cout_stripe = price * pc.STRIPE_PCT + pc.STRIPE_FIXED_EUR
    marge = (price - cout_calcul - cout_stripe) / price
    assert abs(marge - pc.TARGET_MARGIN) < 1e-9


def test_plans_are_consistent():
    assert pc.plan_minutes("free") == 25
    assert pc.plan_minutes("starter") == 75
    assert pc.plan_minutes("pro") == 160
    assert pc.plan_minutes("equipe") == 450
    # Le pack équivaut à Starter côté durée max, marge à part.
    assert pc.CREDIT_PACK["minutes"] == 60
    assert pc.CREDIT_PACK["validity_days"] == 90


def test_global_hard_cap():
    assert pc.GLOBAL_MAX_SOURCE_SECONDS == 4 * 60 * 60
    # Aucun plan ne dépasse le plafond dur.
    for name, cfg in pc.PLANS.items():
        assert cfg["max_video_seconds"] <= pc.GLOBAL_MAX_SOURCE_SECONDS, name


# --- Audit E4 (AUDIT.md #1) : le modèle de coût doit couvrir le calcul et le
# stockage, pas seulement le LLM et la transcription. ------------------------

def test_compute_cost_eur_scales_with_time():
    assert pc.compute_cost_eur(0) == 0
    assert pc.compute_cost_eur(100) > pc.compute_cost_eur(10)
    assert pc.compute_cost_eur(60, vcpus=4) == 2 * pc.compute_cost_eur(60, vcpus=2)


def test_storage_cost_eur_scales_with_bytes_and_retention():
    one_gb = 1024 ** 3
    assert pc.storage_cost_eur(0, retention_days=30) == 0
    assert pc.storage_cost_eur(one_gb, retention_days=30) > 0
    # Deux fois plus de rétention -> deux fois le coût de stockage.
    cost_30d = pc.storage_cost_eur(one_gb, retention_days=30)
    cost_60d = pc.storage_cost_eur(one_gb, retention_days=60)
    assert abs(cost_60d - 2 * cost_30d) < 1e-9


def test_cost_model_constants_are_positive():
    assert pc.MODAL_CPU_EUR_PER_CPU_SECOND > 0
    assert pc.SUPABASE_STORAGE_EUR_PER_GB_MONTH > 0


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"OK  {name}")
    print("Tous les tests passent.")
