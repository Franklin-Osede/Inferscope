import preflight


def sku(description, region, price, usage="OnDemand"):
    units, nanos = int(price), round((price - int(price)) * 1e9)
    return {
        "description": description,
        "serviceRegions": [region],
        "category": {"usageType": usage},
        "pricingInfo": [
            {
                "pricingExpression": {
                    "tieredRates": [
                        {"unitPrice": {"units": "0", "nanos": 0}},
                        {"unitPrice": {"units": str(units), "nanos": nanos}},
                    ]
                }
            }
        ],
    }


# Invented prices that only exercise the arithmetic. They are not real rates.
FIXTURE_SKUS = [
    sku("G2 Instance Core running in Americas", "us-central1", 0.5, usage="Commit1Yr"),
    sku("G2 Instance Core running in Americas", "us-central1", 0.1),
    sku("G2 Instance Ram running in Americas", "us-central1", 0.01),
    sku("Nvidia L4 GPU running in Americas", "us-central1", 1.0),
    sku("Nvidia L4 GPU running in EMEA", "europe-west4", 9.0),
]


def test_parse_env_skips_comments_and_blanks():
    assert preflight.parse_env("# note\n\nA=1\nB=x=y\n") == {"A": "1", "B": "x=y"}


def test_parse_tfvars_reads_quoted_numeric_and_commented_values():
    text = 'project_id = "demo"\nbudget_amount_usd = 30\nregion = "us-central1" # pinned\n'
    assert preflight.parse_tfvars(text) == {
        "project_id": "demo",
        "budget_amount_usd": "30",
        "region": "us-central1",
    }


def test_quota_available():
    quotas = [{"metric": "NVIDIA_L4_GPUS", "limit": 2.0, "usage": 1.0}]
    assert preflight.quota_available(quotas, "NVIDIA_L4_GPUS") == 1.0
    assert preflight.quota_available(quotas, "OTHER") is None


def test_unit_price_uses_last_tier():
    assert preflight.unit_price(sku("x", "r", 1.25)) == 1.25


def test_find_sku_price_ignores_committed_use_and_other_regions():
    price = preflight.find_sku_price(FIXTURE_SKUS, "G2 Instance Core", "us-central1")
    assert price == 0.1
    assert preflight.find_sku_price(FIXTURE_SKUS, "Nvidia L4", "asia-east1") is None


def test_node_hourly_price_sums_components():
    total = preflight.node_hourly_price(FIXTURE_SKUS, "G2", "us-central1", 8, 32, "Nvidia L4", 1)
    assert round(total, 6) == round(8 * 0.1 + 32 * 0.01 + 1.0, 6)


def test_node_hourly_price_is_none_when_a_sku_is_missing():
    assert (
        preflight.node_hourly_price(FIXTURE_SKUS, "G2", "us-central1", 8, 32, "Missing", 1) is None
    )
    assert preflight.node_hourly_price(FIXTURE_SKUS, "N9", "us-central1", 8, 32) is None
