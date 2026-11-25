"""Functions denied by default.

Entries are lowercase. ``package.*`` denies every function of a package. Functions that read
files, call the network, sleep, run commands, execute SQL passed as a string, or change server
state are denied. Contributions welcome: add the function with a corpus case that rejects it.
"""

from __future__ import annotations

# Denied in every dialect: these names are dangerous wherever they exist.
COMMON: frozenset[str] = frozenset(
    {
        "pg_sleep",
        "sleep",
        "benchmark",
        "load_file",
        "pg_read_file",
        "pg_read_binary_file",
        "pg_ls_dir",
        "xp_cmdshell",
        "openrowset",
        "opendatasource",
        "openquery",
        "dblink",
        "dblink_exec",
        "load_extension",
        "readfile",
        "writefile",
        "lo_import",
        "lo_export",
        "utl_http.*",
        "utl_file.*",
        "dbms_pipe.*",
    }
)

BY_DIALECT: dict[str, frozenset[str]] = {
    "postgres": frozenset(
        {
            "pg_stat_file",
            "pg_ls_logdir",
            "pg_ls_waldir",
            "lo_get",
            "lo_unlink",
            "pg_terminate_backend",
            "pg_cancel_backend",
            "pg_reload_conf",
            "pg_rotate_logfile",
            "pg_logical_emit_message",
            "set_config",
            "nextval",
            "setval",
            "pg_advisory_lock",
            "pg_advisory_xact_lock",
            "pg_try_advisory_lock",
            "dblink_connect",
            "dblink_open",
            # These run SQL passed as a string, which would bypass the table allowlist.
            "query_to_xml",
            "query_to_xmlschema",
            "query_to_xml_and_xmlschema",
            "cursor_to_xml",
            "table_to_xml",
            "table_to_xmlschema",
            "table_to_xml_and_xmlschema",
            "schema_to_xml",
            "database_to_xml",
        }
    ),
    "mysql": frozenset(
        {
            "get_lock",
            "release_lock",
            "is_free_lock",
            "master_pos_wait",
            "source_pos_wait",
            "sys_exec",
            "sys_eval",
        }
    ),
    "tsql": frozenset(
        {"xp_dirtree", "xp_fileexist", "xp_regread", "xp_subdirs", "sp_executesql", "openxml"}
    ),
    "oracle": frozenset(
        {
            "dbms_lock.sleep",
            "dbms_session.sleep",
            "dbms_xmlgen.*",
            "dbms_sql.*",
            "dbms_scheduler.*",
            "dbms_java.*",
            "utl_inaddr.*",
            "utl_tcp.*",
            "utl_smtp.*",
            "httpuritype",
        }
    ),
    "sqlite": frozenset({"edit", "fts3_tokenizer"}),
    "duckdb": frozenset(
        {
            "read_csv",
            "read_csv_auto",
            "read_parquet",
            "parquet_scan",
            "read_json",
            "read_json_auto",
            "read_ndjson",
            "read_text",
            "read_blob",
            "glob",
            "sniff_csv",
            "query",
            "query_table",
            "getenv",
            "sqlite_scan",
            "postgres_scan",
            "mysql_scan",
            "iceberg_scan",
            "delta_scan",
        }
    ),
}


def default_denylist(dialect: str) -> frozenset[str]:
    """The built-in denylist for a dialect."""
    return COMMON | BY_DIALECT.get(dialect, frozenset())
