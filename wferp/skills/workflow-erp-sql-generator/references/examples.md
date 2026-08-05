# Examples

## Prompt Example

Input prompt:

`查詢採購單前 20 筆`

Representative output shape:

```sql
SELECT TOP 20
  [DB].[dbo].[TableName].[MK002] AS [年度],
  [DB].[dbo].[TableName].[MK006] AS [本幣借方金額]
FROM [DB].[dbo].[TableName]
```

## Connection Baseline Example

When user provides ODC baseline `Data Source=css04` and `Initial Catalog=CHD`, generated SQL should default to `[CHD].[dbo]` qualifiers unless overridden.

## Command Examples

Build artifacts:

```bash
python3 -m skill_scripts.cli_generate_select --build-artifacts
```

Generate SQL:

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆"
```

Execution validation:

```bash
python3 -m skill_scripts.cli_generate_select --prompt "查詢採購單前 20 筆" --validate-execution --required-columns MK002,MK006 --min-rows 1
```
