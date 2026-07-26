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


if __name__ == "__main__":
    for name in list(globals()):
        if name.startswith("test_"):
            globals()[name]()
            print(f"OK  {name}")
    print("Tous les tests passent.")
