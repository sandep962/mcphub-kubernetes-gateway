import shutil
import subprocess
from pathlib import Path

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
STORE_CHART = REPO_ROOT / "infra" / "helm" / "mcphub-store"
METADATA_CHART = REPO_ROOT / "infra" / "helm" / "mcphub-metadata"
POLICY_TEMPLATE = "templates/metadata-network-policy.yaml"


def _render(chart: Path, release: str, *extra_args: str):
    result = subprocess.run(
        [
            shutil.which("helm"),
            "template",
            release,
            str(chart),
            "--namespace",
            "mcphub",
            *extra_args,
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return [document for document in yaml.safe_load_all(result.stdout) if document]


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required to render the chart")
def test_mariadb_policy_allows_application_and_database_job_pods_only_on_mysql_port():
    documents = _render(
        STORE_CHART,
        "mcphub-store",
        "--set",
        "mariadb.password=helm-test-only",
        "--set",
        "mariadb.rootPassword=helm-test-only",
        "--show-only",
        POLICY_TEMPLATE,
    )
    policy = next(document for document in documents if document["kind"] == "NetworkPolicy")

    assert policy["metadata"]["name"] == "mcphub-mariadb-ingress"
    assert policy["spec"]["podSelector"]["matchLabels"] == {"app": "mcphub-mariadb"}
    assert policy["spec"]["policyTypes"] == ["Ingress"]

    ingress_rule = policy["spec"]["ingress"][0]
    allowed_apps = {
        peer["podSelector"]["matchLabels"]["app"]
        for peer in ingress_rule["from"]
    }
    assert allowed_apps == {
        "mcphub-wordpress",
        "mcphub-gateway",
        "mcphub-inventory-mcp",
        "mcphub-support-mcp",
        "mcphub-db-provisioner",
        "mcphub-schema-migration",
        "mcphub-db-diagnostic",
    }
    assert ingress_rule["ports"] == [{"protocol": "TCP", "port": 3306}]


@pytest.mark.skipif(shutil.which("helm") is None, reason="Helm is required to render the chart")
def test_database_jobs_use_source_labels_permitted_by_mariadb_policy():
    documents = _render(
        METADATA_CHART,
        "mcphub-metadata",
        "--set",
        "schemaMigration.enabled=true",
        "--set",
        "schemaMigration.version=4",
        "--set",
        "schemaMigration.runId=policy-test",
        "--set",
        "databaseDiagnostics.enabled=true",
        "--set",
        "databaseDiagnostics.runId=policy-test",
    )
    job_labels = {
        document["metadata"]["name"]: document["spec"]["template"]["metadata"]["labels"]["app"]
        for document in documents
        if document["kind"] == "Job"
    }

    assert job_labels["mcphub-schema-migration-v4-policy-test"] == "mcphub-schema-migration"
    assert job_labels["mcphub-db-diagnostic-policy-test"] == "mcphub-db-diagnostic"
