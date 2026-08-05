# Test Database (Container)

This folder provides a SQL Server 2019 test database container configured to run with compatibility level 80 (SQL Server 2000 syntax target).

## Start container

```bash
docker compose -f test_db/docker-compose.testdb.yml up -d
```

## Initialize schema/data

After container is healthy, run:

```bash
docker exec -i wferp-mssql-test /opt/mssql-tools18/bin/sqlcmd -C -S localhost -U sa -P Passw0rd!234 -i /init/01_create_wferp_test.sql
```

## Connection environment variables

SQL auth example:

```bash
export DB_DRIVER=mssql
export DB_AUTH_MODE=sql_auth
export DB_CONNECTION_STRING="server=127.0.0.1:1433;user=sa;password=Passw0rd!234;database=wferp_test"
export DB_ENV=test
```

Windows domain style example (for environments where domain auth is available):

```bash
export DB_DRIVER=pyodbc
export DB_AUTH_MODE=windows_domain
export DB_HOST=sqlserver.company.local
export DB_PORT=1433
export DB_DATABASE=wferp_test
export DB_DOMAIN=ACME
export DB_USERNAME=svc_sql_reader
export DB_ENV=test
```

> Note: Linux containers cannot provide native Windows AD integrated auth by themselves. Domain auth support is exposed via config and driver path for environments that provide it.
