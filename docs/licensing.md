# Licensing and attribution

The SDK and example plugin source are offered under the MIT License in the
repository root. The same license text is included with each distributable
package. The available Git history identifies Tyler Ungerman as the author of
the initial public commit; the copyright notice follows that repository
identity. Contributors should retain attribution notices in distributed
copies.

This is a source-code licence. It does not grant rights to map, imagery,
elevation or other data returned by a provider or supplied by a user. Review
each source's current terms before fetching, storing, redistributing or
publishing derived data. Preserve required attribution and record the source
licence in `SourceRecord` or the local extract manifest when known. A local
manifest is a publisher-supplied claim; its checksum does not authenticate the
claim.

## Direct Python dependencies

The core package's only runtime dependency is Pydantic (`pydantic>=2.5,<3`),
whose upstream project declares MIT. Optional extras and development metadata
declare:

- `boto3` for R2 storage: Apache-2.0.
- `folium` for map output: MIT.
- `pyproj` for coordinate transformations: MIT; pyproj also packages PROJ,
  whose bundled licence and data notices must be retained by that package.
- `pytest` for development and tests: MIT.

The independent example plugin depends on this SDK package; it introduces no
additional runtime dependency. Generated plugins add Pydantic through the SDK
and pytest only as an optional development dependency. The table below records
the environment from a clean Python 3.12 install of the SDK `dev` extra, the
example plugin and test tooling, scanned with `pip-licenses` on 18 November
2026. It includes transitive dependencies. Versions and dependency graphs can
change.

| Package | Version | Declared or transitive licence |
| --- | --- | --- |
| annotated-types | 0.8.0 | MIT |
| boto3 | 1.44.5 | Apache-2.0 |
| botocore | 1.44.5 | Apache-2.0 |
| branca | 0.8.2 | MIT |
| certifi | 2026.10.23 | MPL-2.0 |
| charset-normalizer | 3.5.2 | MIT |
| Folium | 1.0.0 | MIT |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| Jinja2 | 3.1.6 | BSD |
| jmespath | 1.1.0 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| NumPy | 2.5.5 | BSD-3-Clause, 0BSD, MIT, Zlib, CC0-1.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pluggy | 1.7.0 | MIT |
| Pydantic | 2.14.3 | MIT |
| pydantic-core | 2.50.4 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pyproj | 3.8.0 | MIT |
| pytest | 9.2.0 | MIT |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD |
| requests | 2.34.2 | Apache-2.0 |
| s3transfer | 0.19.3 | Apache-2.0 |
| six | 1.17.0 | MIT |
| typing-extensions | 4.16.0 | PSF-2.0 |
| typing-inspection | 0.4.5 | MIT |
| urllib3 | 2.8.0 | MIT |
| xyzservices | 2026.9.1 | BSD-3-Clause |

This resolution includes separate MPL-2.0 `certifi` and multi-licensed
NumPy. These packages are installed as dependencies; their source is not
copied into this repository. Retain and review notices supplied by the exact
wheel artifacts when redistributing them. In particular, `pyproj` wheels
include PROJ and data licence notices. This point-in-time scan does not replace
checking the exact artifacts selected for a release.

## Data sources and bundled data

The OSM adapters query OpenStreetMap services. OSM database contents are
available under the Open Database License (ODbL), with attribution to
OpenStreetMap contributors. The SDK code and MIT licence do not relicense OSM
data. The remote DEM smoke adapter uses OpenTopoData's SRTM90m service; review
that service's source-data terms and attribution for the dataset selected.
Local extracts retain the licence and attribution provided in their manifest;
unknown terms remain unknown and must be resolved by the data provider before
redistribution.

The SDK and example-plugin test fixtures are synthetic and do not contain an
ORBID image, model, cache or benchmark. Private ORBID assets are outside the
public source tree and are not licensed by this repository notice.

## References reviewed for this alpha

- [Pydantic open-source licensing](https://pydantic.dev/opensource) identifies
  its open-source packages as MIT-licensed.
- Upstream [boto3 LICENSE](https://github.com/boto/boto3/blob/develop/LICENSE)
  is Apache-2.0; [Folium LICENSE](https://github.com/python-visualization/folium/blob/main/LICENSE.txt)
  and [pyproj metadata](https://github.com/pyproj4/pyproj/blob/main/pyproject.toml)
  declare MIT. PROJ's [citation metadata](https://github.com/OSGeo/PROJ/blob/master/CITATION.cff)
  declares MIT. Inspect licence files bundled with the actual wheel, including
  PROJ and transitive dependencies, when producing a release.
- [OpenStreetMap licensing and attribution](https://www.openstreetmap.org/copyright)
  describes the ODbL data licence and required attribution.
- [USGS SRTM archive](https://www.usgs.gov/centers/eros/science/usgs-eros-archive-digital-elevation-shuttle-radar-topography-mission-srtm)
  identifies SRTM data as public domain. Open Topo Data documents the
  [SRTM dataset](https://www.opentopodata.org/datasets/srtm/) and
  [public API limits](https://www.opentopodata.org/). API availability terms
  do not change the rights attached to source datasets.

This inventory is a release checklist, not legal advice or a substitute for
checking upstream notices and the exact artifacts included in a distribution.
