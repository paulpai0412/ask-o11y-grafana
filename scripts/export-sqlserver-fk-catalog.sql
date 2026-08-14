SET NOCOUNT ON;
SELECT
    s.name AS [schema],
    fk.name AS constraint_name,
    parent_table.name AS from_table,
    parent_column.name AS from_column,
    referenced_table.name AS to_table,
    referenced_column.name AS to_column,
    fk_columns.constraint_column_id AS ordinal,
    fk.is_disabled,
    fk.is_not_trusted
FROM sys.foreign_keys AS fk
JOIN sys.foreign_key_columns AS fk_columns
  ON fk_columns.constraint_object_id = fk.object_id
JOIN sys.tables AS parent_table
  ON parent_table.object_id = fk.parent_object_id
JOIN sys.schemas AS s
  ON s.schema_id = parent_table.schema_id
JOIN sys.columns AS parent_column
  ON parent_column.object_id = parent_table.object_id
 AND parent_column.column_id = fk_columns.parent_column_id
JOIN sys.tables AS referenced_table
  ON referenced_table.object_id = fk.referenced_object_id
JOIN sys.columns AS referenced_column
  ON referenced_column.object_id = referenced_table.object_id
 AND referenced_column.column_id = fk_columns.referenced_column_id
ORDER BY s.name, fk.name, fk_columns.constraint_column_id
FOR JSON PATH, ROOT('foreign_keys');
