#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.9"
# dependencies = ["typer"]
# ///
"""
One-command post-run upload for GW-RePO: runs lineage-import/submit_lineage.py,
co2-import/submit_co2.py and multiqc-import/submit_multiqc.py back to back against
the same pipeline run.

Usage:
    uv run submit_run.py [--run-dir .] [--endpoint http://localhost:80] [--dry-run]

    (or: ./submit_run.py ..., once it's executable -- the shebang runs it via
    `uv run --script`; or `python submit_run.py ...` with `typer` already installed)

Needs GWREPO_API_KEY in the environment (passed through to both importers unchanged).
Reads GWREPO_URL as the --endpoint default, same as gwrepo.config's tower endpoint.

Unlike lineage-import/submit_lineage.py and co2-import/submit_co2.py, which are
stdlib-only by design (see their READMEs -- they're meant to run with no setup),
this wrapper needs `typer`. The `# /// script` block above declares that as an
inline dependency (PEP 723): `uv run` reads it and transparently creates an
isolated environment with just `typer` on first run, no separate `pip install`
step and no risk of colliding with anything else on the system. It's a
convenience entry point on top of the other two scripts, not something else
depends on it, so this tradeoff (needing `uv`) is scoped to just this one script.
"""

import subprocess
import sys
from pathlib import Path
from typing import Optional

import typer

app = typer.Typer(add_completion=False)

REPO_ROOT = Path(__file__).resolve().parent
LINEAGE_SCRIPT = REPO_ROOT / "lineage-import" / "submit_lineage.py"
CO2_SCRIPT = REPO_ROOT / "co2-import" / "submit_co2.py"
MULTIQC_SCRIPT = REPO_ROOT / "multiqc-import" / "submit_multiqc.py"


def _run(script: Path, args: list, label: str) -> int:
    typer.echo(f"\n=== {label} ===")
    return subprocess.run([sys.executable, str(script), *args]).returncode


@app.command()
def main(
    run_dir: str = typer.Option(".", help="dir the pipeline ran in"),
    endpoint: str = typer.Option("http://localhost:80", envvar="GWREPO_URL", help="GW-RePO API base URL"),
    workflow_id: Optional[str] = typer.Option(
        None, help="override the workflow id (default: resolved from .nextflow/history)"
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="print what would be POSTed, don't POST"),
    skip_lineage: bool = typer.Option(False, help="don't run lineage-import"),
    skip_co2: bool = typer.Option(False, help="don't run co2-import (e.g. nf-co2footprint wasn't enabled)"),
    skip_multiqc: bool = typer.Option(False, help="don't run multiqc-import (e.g. the pipeline has no MultiQC report)"),
    multiqc_data: Optional[str] = typer.Option(
        None, help="path to multiqc_data.json (default: newest under --run-dir)"
    ),
):
    """Steer the complete post-run upload to the GW-RePO API: file provenance
    (lineage-import), CO2/energy (co2-import) and per-sample MultiQC metrics
    (multiqc-import), all matched to the workflow and ProcessExecution rows the
    tower-emulation endpoints already created live."""
    common = ["--run-dir", run_dir, "--endpoint", endpoint]
    if workflow_id:
        common += ["--workflow-id", workflow_id]
    if dry_run:
        common.append("--dry-run")

    results = {}
    if not skip_lineage:
        results["lineage-import"] = _run(LINEAGE_SCRIPT, common, "lineage-import")
    if not skip_co2:
        results["co2-import"] = _run(CO2_SCRIPT, common, "co2-import")
    if not skip_multiqc:
        extra = ["--multiqc-data", multiqc_data] if multiqc_data else []
        results["multiqc-import"] = _run(MULTIQC_SCRIPT, common + extra, "multiqc-import")

    typer.echo("\n=== summary ===")
    if not results:
        typer.echo("  nothing ran -- --skip-lineage, --skip-co2 and --skip-multiqc all given")
        raise typer.Exit(code=0)

    failed = False
    for name, code in results.items():
        typer.echo(f"  {name}: {'ok' if code == 0 else f'FAILED (exit {code})'}")
        failed = failed or code != 0

    raise typer.Exit(code=1 if failed else 0)


if __name__ == "__main__":
    app()
