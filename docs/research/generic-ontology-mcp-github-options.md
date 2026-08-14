# Generic Ontology MCP：GitHub 套件選型與重構建議

> 查核日：2026-08-13
> 範圍：把目前只支援 `u1-operating-daily`／`random_forest_shap` 的 PoC 泛化為可服務 CSV、關聯式 schema、WFERP metadata、observability 與 event datasets 的 read-only Ontology MCP。
> 方法：優先查官方 GitHub source、release、規格與一手文件；既有 Semantica 深度查核沿用 [`ask-o11y-semantica-ontology-analysis.md`](./ask-o11y-semantica-ontology-analysis.md)。

## 結論

**不採任何現成 ontology／metadata 平台作 production runtime。** 建議維持目前的 `PyYAML + jsonschema + stdlib`，把缺少的通用能力做成薄層：

1. bounded-context snapshot catalog/resolver；
2. datasource-neutral Candidate IR；
3. CSV／SQL catalog／metadata JSON／OpenTelemetry importer；
4. canonical JSON compiler + SHA-256 + approval/evidence ledger；
5. read-only bounded projection tools；
6. 位於 Planner 的 consumer-specific deterministic validators。

只建議新增一個 production dependency：**SQLGlot**，但放在 Data Query Planner，而不是 Ontology MCP。它用 AST 取代 WFERP 現有 regex table/JOIN 解析，讓 approved relation、join key、cardinality、fanout 與 SQL policy 可被 deterministic 驗證。SQLGlot 是 parser/transpiler，不是授權器；parse 成功不能取代本案的 semantic gate。[SQLGlot README](https://github.com/tobymao/sqlglot/blob/aa3e8f2de7c12964e3727dc8b8fd143eef7d40a5/README.md) · [package metadata](https://github.com/tobymao/sqlglot/blob/aa3e8f2de7c12964e3727dc8b8fd143eef7d40a5/pyproject.toml)

## 需求與目前 hardcode

目前實作不是通用 ontology runtime：

- `ontology_contract.py` 的 `DEFAULT_SNAPSHOT` 固定 U1 snapshot；
- `scripts/build-ontology-snapshot.py` 的 registry/output default 固定 U1；
- validator 固定 `kind == random_forest_shap`、target/features/split/seed；
- registry schema 以 ML dataset、target、feature allowlist 為中心；
- Ontology MCP 只有單 snapshot，缺 namespace/catalog、asset/entity/relation/metric/event/state；
- `data-query-planner-mcp/wferp_sql.py` 用 regex 提取 `FROM/JOIN`，且 heuristic relationship edges 尚不是 approved join authority。

因此 U1 應降為第一個普通 fixture；SHAP policy 應移至 ML Planner validator，不能留在 ontology core。

## 不可違反的產品邊界

- Ontology MCP 只讀、bounded、snapshot-pinned；沒有 datasource credentials、raw rows、mutation、任意 SPARQL/Cypher 或 full graph dump。
- Importer 只能產 `observed`／`proposed` Candidate IR；只有 steward/Git review 能產 `approved`。
- Ontology MCP 提供 declarations/context，不是 final enforcement point。
- Planner 的 deterministic validator 才能授權 executable plan。
- Grafana Query 仍是唯一 datasource executor。
- Proposed relationship 可協助 candidate table discovery，但必須標記 `executable: false`。

## 套件／規格決策矩陣

| 項目 | 一手能力與限制 | Python／授權／成本 | 判定 |
| --- | --- | --- | --- |
| **PyYAML + jsonschema + stdlib** | 現有 repo 已用 YAML authoring、JSON Schema shape validation、canonical JSON/hash；足以實作 immutable catalog。 | 已鎖定；最小依賴。 | **採用：production core** |
| **SQLGlot** | 官方 repo 提供多 dialect parser、AST、optimizer、transpiler與 lineage API；需指定 dialect，且 parser 本身偏寬容。[source](https://github.com/tobymao/sqlglot/blob/aa3e8f2de7c12964e3727dc8b8fd143eef7d40a5/README.md) | MIT；純 Python package，metadata 要求 Python `>=3.10`。[pyproject](https://github.com/tobymao/sqlglot/blob/aa3e8f2de7c12964e3727dc8b8fd143eef7d40a5/pyproject.toml) | **採用：Planner-only AST gate** |
| **Frictionless Table Schema** | JSON tabular schema具有 fields/type/format/constraints/foreignKeys，適合作 CSV/Excel importer 的 physical schema vocabulary。[spec](https://specs.frictionlessdata.io/table-schema/) | 規格本身無 runtime 成本。 | **採規格概念** |
| **frictionless-py 5.19.0** | 可 describe/extract/validate/transform CSV/Excel/SQL，並宣告 Python 3.14 classifier；但直接依賴 petl、Pydantic、Jinja、requests、Typer 等多包。[pyproject](https://github.com/frictionlessdata/frictionless-py/blob/43a63e0be8f332f82177f62e0099e667a93bd77b/pyproject.toml) | MIT；對只需離線 header/schema importer 過重。 | **延後；先不採 runtime** |
| **OpenLineage 1.52.0** | 正式 JSON Schema/OpenAPI，以 Run/Job/Dataset + facets 表達 lineage；custom facet 要有 distinct prefix 與 immutable versioned `_schemaURL`。[spec](https://github.com/OpenLineage/OpenLineage/blob/cfd47d6f3e1b13167136b2508768c94a2351af23/spec/OpenLineage.md) | Apache-2.0；若只借 vocabulary 無需 client/runtime。 | **採 vocabulary/projection，不採服務** |
| **OpenTelemetry Semantic Conventions** | 官方以 YAML model定義 attribute、metric、event、entity conventions，產 registry/docs/schema；適合作 observability importer 的 canonical aliases、units與stability來源。[model](https://github.com/open-telemetry/semantic-conventions/blob/7f3c3bfc300cc090871692219af6a2495aa67915/model/README.md) | Apache-2.0；只 pin release/model，不嵌 runtime。 | **採 vocabulary/import source** |
| **LinkML** | 一份 YAML model可產 JSON Schema、SHACL、Python/TS/SQL等 artifacts；官方將 `linkml`定位為 developer-time compiler。[generators](https://linkml.io/linkml/generators/index.html) | Apache-2.0；目前 package metadata只列 Python 3.10–3.13，且 direct dependency面廣：RDFLib、Pydantic、SQLAlchemy、ANTLR、Jinja等。[pyproject](https://github.com/linkml/linkml/blob/a7ed3e4cbb19731f072d0d90b6d52f7d822569ee/packages/linkml/pyproject.toml) | **延後 authoring/codegen PoC** |
| **RDFLib + pySHACL** | RDFLib 提供 RDF stores、serialization與 SPARQL query/update；pySHACL 對 SHACL shapes 回 formal validation report，也支援 rules/inference與 remote mode。[RDFLib](https://github.com/RDFLib/rdflib/blob/8b32146a0f9cb748c2068662e50d02b42fc86dfd/README.md) · [pySHACL](https://github.com/RDFLib/pySHACL/blob/469cca7a22a078b36c167c1e8dadecf5e5ec6c75/README.md) | BSD-3-Clause／Apache-2.0；pySHACL README只概稱 3.8+，未提供本案所需明確 3.14 assurance。SHACL pass只證明資料符合該 shapes，不證明 business semantics正確。 | **隔離 validation/interchange PoC** |
| **pyoxigraph** | Rust-backed in-memory/disk RDF store，支援 SPARQL 1.1與多種 RDF format。[source](https://github.com/oxigraph/oxigraph/blob/d6f5b98941f4b0d1f09d8ce822929670a6a359a6/python/README.md) | MIT/Apache-2.0；native wheel/storage與任意 SPARQL面超出目前 bounded JSON需求。 | **延後到實測 traversal/scale 瓶頸** |
| **Owlready2** | OWL-oriented Python API/reasoning/store；不是 snapshot catalog、candidate governance或 planner gate。 | LGPL-3.0；PyPI 0.51 classifiers只到 Python 3.10，3.14風險未消除。[PyPI](https://pypi.org/project/Owlready2/) | **拒絕** |
| **MetricFlow** | 定義 metrics並編譯多跳 joins/ratio/cumulative/time grain為 engine SQL；官方明說依賴 working dbt project + adapter。[README](https://github.com/dbt-labs/metricflow/blob/e4b9b7bf673a6fee0310c8f9a05b5a6d13336c1a/README.md) | >=0.209 Apache-2.0，Python 3.14 declared；但引入 dbt query compiler/execution worldview，重疊 Planner/Grafana boundary。 | **拒絕作 ontology runtime；未來 metric-only另評估** |
| **Cube** | YAML cube model含 sql table、measures、dimensions、joins，並由請求生成 SQL。[official model docs](https://github.com/cube-js/cube/blob/f7822cac0d4b16d8ee197e793192f5068cb8895a/docs/content/product/data-modeling/overview.mdx) | 完整 headless semantic/query platform；會成第二 execution/query plane。 | **拒絕** |
| **DataHub** | 完整 metadata graph、ingestion、lineage、governance、semantic model等平台。 | 大型 Java/Python/GraphQL服務與 connector ecosystem；遠超 read-only bounded snapshot需求。 | **拒絕 runtime；可選離線 export importer** |
| **OpenMetadata** | schema-first metadata platform，含 connectors、catalog、lineage、governance與多服務部署。[official repo](https://github.com/open-metadata/OpenMetadata/blob/293423359d49a6f84a7f1996cb3ea276da7771c4/README.md) | 大型 Java/Python/Elasticsearch/DB平台；會重複既有 governance/runtime。 | **拒絕 runtime；可選離線 export importer** |
| **Semantica 0.6.5** | 有 RDF/OWL/SHACL/provenance與 connector components，但無 shipping generic semantic-layer API；built-in MCP有 mutation/raw graph query，placeholder validator不能作 gate。[既有一手查核](./ask-o11y-semantica-ontology-analysis.md) | MIT但 core dependency極重；v0.6.5 security release修正 auth/SSRF/Cypher/SPARQL injection等問題。[tag](https://github.com/semantica-agi/semantica/releases/tag/v0.6.5) | **拒絕 production runtime；只保留隔離對照** |

## 推薦最小 stack

### Production runtime

```text
Python stdlib
PyYAML 6.0.3
jsonschema 4.19.2
SQLGlot（固定版本；只在 Data Query Planner）
```

不要在 Ontology MCP runtime 加 RDFLib、SHACL、LinkML、graph DB、DataHub/OpenMetadata client、MetricFlow或Semantica。

### 採用但不安裝的規格/vocabulary

- JSON Schema Draft 2020-12：registry/catalog/contract shape；
- Frictionless Table Schema：tabular physical schema/import vocabulary；
- OpenLineage Dataset/column-lineage/custom facet概念：lineage evidence projection；
- OpenTelemetry Semantic Conventions：observability canonical terms/units/stability；
- W3C PROV-O/SHACL：只作可選 export/validation interoperability。

## 通用資料模型

Snapshot core不應含特定分析方法。最小一等物件：

```text
namespace / bounded_context
physical_asset
entity
field
key
relation
metric
event
state
code_set
policy
assumption
evidence
approval
provenance
```

共同 assertion metadata：

```yaml
status: observed | proposed | approved | rejected | deprecated
evidence: [...]
approval: {owner, effective_from, effective_to, record_ref}
```

`physical_asset.kind`至少支援 `tabular_file | sql_table | timeseries | log_stream | event_topic | api_resource`。Relation帶方向、physical keys、cardinality、optionality、fanout policy、evidence level與 executable flag。Field帶 type/unit/semantic kind/availability/lineage/physical mappings；analysis role由 policy/context決定，而非永遠固化在 field。

## Catalog、snapshot與 Candidate IR

```text
semantic/
  catalog.json
  schema/
    catalog.schema.json
    candidate-ir.schema.json
    snapshot.schema.json
  registry/<bounded-context>/*.yaml
  snapshots/<snapshot-id>.json
  importers/
    tabular.py
    sql_catalog.py
    metadata_bundle.py
    otel_semconv.py
```

Catalog entry：

```json
{
  "snapshot_id": "erp-procurement-v1",
  "namespace": "erp.procurement",
  "datasets": ["wferp"],
  "path": "snapshots/erp-procurement-v1.json",
  "sha256": "...",
  "status": "approved",
  "effective_from": "..."
}
```

所有 importer輸出同一 Candidate IR，且 schema禁止 importer輸出 `approved`：

```json
{
  "source_snapshot": {"kind":"...","ref":"...","sha256":"...","captured_at":"..."},
  "assets": [],
  "field_candidates": [],
  "key_candidates": [],
  "relation_candidates": [],
  "metric_candidates": [],
  "limitations": []
}
```

Promotion只能經 Git PR/steward record。Compiler驗 schema、references、status/approval/effective period，輸出 sorted canonical JSON及 hash；catalog亦 canonical/hash-checked。

## 通用 MCP surface

建議保持五個 bounded read-only tools，而非暴露 graph language：

1. `list_snapshots(namespace?, dataset_id?)`
2. `resolve_concepts(terms, namespace?, dataset_ids?, snapshot_ref?)`
3. `get_bounded_context(intent, concept_ids?, dataset_ids?, limits, snapshot_ref)`
4. `get_field_semantics(field_refs, snapshot_ref)`
5. `get_relation_paths(from_ids, to_ids, max_hops<=3, include_proposed=false, snapshot_ref)`

Metrics可先包含於 bounded context；若回應過大再新增 `get_metric_definition`。工具永不產 SQL。`get_relation_paths`預設只回 approved path；proposed path即使被要求，也必須回 `executable:false`。

## Validator分層

Ontology core不得 dispatch `random_forest_shap`。Planner分開維護：

- `validate_relational_query_plan`：asset/field scope、approved relation、join keys、cardinality、fanout、date/code semantics；
- `validate_ml_feature_plan`：target/features、availability/as-of、lineage/leakage、quality/split；
- `validate_timeseries_plan`：metric、time identity、unit、aggregation/downsampling、timezone/late data；
- `validate_metric_query_plan`：formula、grain、unit/currency、approved relation paths。

SQLGlot只負責把 SQL轉 AST並可靠列出 tables、columns、aliases、JOIN predicates；validator再將 AST對照 snapshot。不得讓 SQLGlot自行推定 approved relation或放行 SQL。

## 對目前 repo 的具體重構 seam

| 現有位置 | 調整 |
| --- | --- |
| `ontology_contract.py` | 拆成 catalog loader、snapshot verifier、bounded projector；移除 `DEFAULT_SNAPSHOT`與 SHAP validator。 |
| `scripts/build-ontology-snapshot.py` | 改為 generic compiler：輸入 registry/catalog，輸出任意 bounded-context snapshot；U1值只留 fixture。 |
| `semantic/schema/registry.schema.json` | 升級為 generic asset/entity/field/key/relation/metric/event/state schema；ML policy另檔。 |
| `ontology-mcp/server.py` | 依 namespace/dataset/snapshot resolver查 catalog；改用通用 tools與 response caps。本階段不實作 access policy、hidden-field security 或 action。 |
| `data-query-planner-mcp/server.py` | 把現有 SHAP規則移入 `validators/ml.py`；選 validator由 typed plan contract決定。 |
| `data-query-planner-mcp/wferp_sql.py` | ranking可保留作 candidate discovery；regex SQL extraction改 SQLGlot AST；新增 approved relation/JOIN gate。 |
| `data-query-planner-mcp/metadata/wferp/*.json` | 經 metadata-bundle importer轉 Candidate IR；所有 heuristic edges預設 proposed/non-executable。 |
| `grafana-query-mcp/server.py`、`sandbox-analysis-mcp/server.py` | 保留 snapshot/plan hash驗證，不載入任何 datasource ontology library。 |
| `scripts/run-ask-o11y-sandbox-shap-e2e.py` | U1 hash改由 catalog/tool result取得，不 hardcode；保留為 ML fixture。 |

## 遷移與驗證階段

### Phase 1：去除 runtime hardcode

- catalog schema/resolver；
- U1 snapshot成普通 catalog entry；
- snapshot/hash只從 resolver取得；
- SHAP validator移到 Planner ML validator。

驗收：換入第二個小 CSV registry不改 Ontology MCP code。

### Phase 2：通用 schema與 import pipeline

- generic snapshot schema + Candidate IR；
- tabular importer採 Frictionless vocabulary概念；
- compiler強制 candidate不可 approved。

驗收：U1原安全/負向 fixtures結果不變，snapshot build可重現。

### Phase 3：WFERP relational fixture

- 匯入 `schema_bundle/field_index/alias_index/primary_key_map/relationship_edges`；
- table/field可 observed，asserted PK與 heuristic relations維持 proposed；
- SQLGlot AST validator；
- 先由 steward核准少數真實 relation。

驗收：候選 table可被 context選出；單表 query可通過；heuristic-only JOIN回 `JOIN_RELATION_NOT_APPROVED`且 Grafana calls=0；approved JOIN才可執行。

### Phase 4：Observability/event fixture

- pin一版 OpenTelemetry Semantic Conventions；
- 匯入 metric/log/event/entity attributes、unit、stability；
- 用 OpenLineage-style facet表示 source/version/column lineage evidence。

驗收：同一 MCP code可解析 U1、WFERP與observability三種形態；新增 fixture不修改 core。

### Phase 5：可選 interoperability PoC

只有出現外部 RDF/SHACL consumer時，才在隔離 CI job測 LinkML或 RDFLib/pySHACL export；與 hand-authored JSON Schema、golden fixtures比對。不得把 SHACL pass升格為 domain approval。

## 主要風險與待決策

1. **Steward治理是最大風險**：沒有 owner/quorum/revoke SLA，generic registry仍只是一份字典。
2. **SQLGlot dialect**：需用 `tsql` golden fixtures驗證舊 SQL Server語法；parser upgrade必須 pin並跑 AST fixtures。
3. **關聯證據**：PK map/lineage/同名欄都不等於 FK/cardinality；要 profile uniqueness/orphan/fanout並由 steward核准。
4. **跨 snapshot identity**：同一 concept跨 bounded contexts合併需 explicit mapping，不靠名稱自動合併。
5. **OpenTelemetry/OpenLineage版本漂移**：importer必須 pin upstream commit/release與 source hash。
6. **Python 3.14**：SQLGlot與 Frictionless有明確相容訊號；LinkML/pySHACL/Owlready2在採用前仍需 isolated install/smoke test。
7. **權限投影**：catalog coverage不代表 caller有權看到所有 physical mappings；bounded context需加入 caller authorization filter。
8. **規模門檻**：JSON snapshots在實測 latency/size超限前，不導入 Oxigraph/graph DB；需先訂 max assets/fields/relations/bytes與 benchmark。

## Scope decision（2026-08-13）

第一階段明確不處理 access policy、property-level authorization、row/cell security、hidden-field security behavior 或 action definition/execution。若未來加入 `hidden` metadata，它只代表 discovery/UI projection，不是安全邊界。

## 最終採用方案

**現在採用：** `PyYAML + jsonschema + stdlib` generic catalog/compiler/projector，及 Planner-only pinned SQLGlot `30.17.0`。實作驗證已全量匯入 WFERP 1,369 tables／32,022 fields／1,178 proposed relations；Candidate IR無 approved candidates，proposed JOIN由Planner以 `JOIN_RELATION_NOT_APPROVED`拒絕。
**採規格不採 runtime：** Frictionless Table Schema、OpenLineage、OpenTelemetry Semantic Conventions。
**延後隔離 PoC：** LinkML、RDFLib/pySHACL、pyoxigraph。
**拒絕 production runtime：** Owlready2、DataHub、OpenMetadata、Cube、MetricFlow、Semantica。

這個方案不把「通用」誤解成導入通用 graph平台；它把通用性放在穩定 IR、bounded snapshots、importer contract與consumer validators，維持 Ask O11y既有信任邊界。
