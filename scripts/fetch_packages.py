"""Download the pinned FHIR packages into the local FHIR package cache (~/.fhir/packages).

The HL7 validator normally downloads packages itself from packages.fhir.org. This script
exists so the project also works where that registry is unreachable (locked-down networks,
CI caches): it tries packages.fhir.org first, then the official HL7 mirrors on the npm
registry, and verifies npm downloads against their published sha512 integrity hash.

Usage:  python scripts/fetch_packages.py
        python scripts/fetch_packages.py --allow-substitutes   (restricted networks only)

--allow-substitutes: when an exact version can't be downloaded anywhere, install a small alias
package or the nearest published release in its place. This is a
workaround for locked-down build environments only. It is recorded in ~/.fhir/packages/SUBSTITUTIONS.txt and printed by
`make validate`, so results produced with substitutes are never mistaken for a clean run.
Never use it in CI or for numbers you publish.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import sys
import tarfile
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE = Path.home() / ".fhir" / "packages"
TIMEOUT = 60

# Some packages are only mirrored on npm under the official @hl7 scope.
NPM_NAMES = {"hl7.fhir.r4.core": "@hl7/hl7.fhir.r4.core"}


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json, application/gzip"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        return resp.read()


def from_fhir_registry(name: str, version: str) -> bytes:
    return _get(f"https://packages.fhir.org/{name}/{version}")


def from_npm(name: str, version: str) -> bytes:
    npm_name = NPM_NAMES.get(name, name)
    meta = json.loads(_get(f"https://registry.npmjs.org/{npm_name.replace('/', '%2f')}"))
    if version not in meta.get("versions", {}):
        available = ", ".join(meta.get("versions", {}).keys()) or "none"
        raise LookupError(f"{npm_name}@{version} not on npm (available: {available})")
    dist = meta["versions"][version]["dist"]
    data = _get(dist["tarball"])
    algo, expected = dist["integrity"].split("-", 1)
    actual = base64.b64encode(hashlib.new(algo, data).digest()).decode()
    if actual != expected:
        raise ValueError(f"integrity check failed for {npm_name}@{version}")
    return data


def install(name: str, version: str, data: bytes) -> None:
    target = CACHE / f"{name}#{version}"
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        tar.extractall(target, filter="data")
    if not (target / "package" / "package.json").exists():
        raise ValueError(f"{name}#{version}: archive has no package/package.json")


def npm_latest(name: str) -> str:
    npm_name = NPM_NAMES.get(name, name)
    meta = json.loads(_get(f"https://registry.npmjs.org/{npm_name.replace('/', '%2f')}"))
    return meta["dist-tags"]["latest"]


def normalise_npm_r4_core(target: Path) -> int:
    """The npm mirror of hl7.fhir.r4.core also ships ~6,800 single-element 'data element'
    logical models (ids like 'Claim.prescription') that the canonical package from
    packages.fhir.org does not. They add nothing to validation but exhaust the validator's
    memory, so remove them and let the validator rebuild its index."""
    pkg = target / "package"
    removed = 0
    for path in pkg.glob("StructureDefinition-*.*.json"):
        sd = json.loads(path.read_text())
        if "." in sd.get("id", "") and sd.get("kind") == "logical":
            path.unlink()
            removed += 1
    (pkg / ".index.json").unlink(missing_ok=True)
    with (CACHE / "SUBSTITUTIONS.txt").open("a") as log:
        log.write(f"hl7.fhir.r4.core#4.0.1 from npm: removed {removed} data-element logical models\n")
    return removed


def write_stub(name: str, version: str, pinned: str) -> None:
    """An empty package for a version some dependency asks for. Its content is already
    loaded through the pinned version, so the stub carries no content and no dependencies
    (dependencies on stubs can form loops the validator follows forever)."""
    stub = CACHE / f"{name}#{version}" / "package"
    stub.mkdir(parents=True, exist_ok=True)
    (stub / "package.json").write_text(json.dumps({
        "name": name, "version": version, "fhirVersions": ["4.0.1"], "type": "IG",
        "description": f"LOCAL STUB: content provided by pinned {name}#{pinned}",
        "dependencies": {},
    }, indent=2))
    with (CACHE / "SUBSTITUTIONS.txt").open("a") as log:
        log.write(f"{name}#{version} -> empty stub (content from pinned {name}#{pinned})\n")


def pinned_name(name: str, pins: dict[str, str]) -> str | None:
    """'hl7.terminology' (R5 naming) and 'hl7.terminology.r4' are the same content for us."""
    return next((n for n in (name, f"{name}.r4") if n in pins), None)


def fetch_one(name: str, version: str, pins: dict[str, str], allow_substitutes: bool) -> str:
    """Install one package; return a one-line status."""
    if (CACHE / f"{name}#{version}" / "package" / "package.json").exists():
        return f"  cached   {name}#{version}"
    last_error: Exception | None = None
    for source, fetch in (("packages.fhir.org", from_fhir_registry), ("npm", from_npm)):
        try:
            install(name, version, fetch(name, version))
            if source == "npm" and name == "hl7.fhir.r4.core":
                removed = normalise_npm_r4_core(CACHE / f"{name}#{version}")
                return f"  fetched  {name}#{version}  (npm, normalised: -{removed} data elements)"
            return f"  fetched  {name}#{version}  ({source})"
        except (urllib.error.URLError, LookupError, ValueError, OSError) as exc:
            last_error = exc
    if not allow_substitutes:
        return f"  MISSING  {name}#{version}: {last_error}"
    try:
        pinned = pinned_name(name, pins)
        if pinned and pins[pinned] != version:
            write_stub(name, version, pins[pinned])
            return f"  STUB     {name}#{version}  (content from pinned {pinned}#{pins[pinned]})"
        nearest = npm_latest(name)
        install(name, version, from_npm(name, nearest))
        with (CACHE / "SUBSTITUTIONS.txt").open("a") as log:
            log.write(f"{name}#{version} <- content of {nearest}\n")
        return f"  SUBST    {name}#{version}  <- content of {nearest}"
    except (urllib.error.URLError, LookupError, ValueError, OSError, KeyError) as exc:
        return f"  MISSING  {name}#{version}: {exc}"


def main() -> int:
    allow_substitutes = "--allow-substitutes" in sys.argv[1:]
    extra = [a for a in sys.argv[1:] if "#" in a]  # e.g. hl7.terminology.r4#6.2.0
    config = tomllib.loads((ROOT / "versions.toml").read_text())
    pins = {**config["packages"], **config.get("support_packages", {})}
    results = [fetch_one(n, v, pins, allow_substitutes) for n, v in pins.items()]
    results += [fetch_one(*a.split("#", 1), pins, allow_substitutes) for a in extra]
    print("\n".join(results))
    missing = [r for r in results if r.lstrip().startswith("MISSING")]
    if missing:
        print(f"\n{len(missing)} package(s) missing. The validator will try to fetch them itself.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
