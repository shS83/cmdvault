-- Run interactively as the PostgreSQL administrator; never paste secrets here.
CREATE ROLE cmdvault LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password cmdvault
CREATE DATABASE cmdvault OWNER cmdvault;
REVOKE ALL ON DATABASE cmdvault FROM PUBLIC;
\connect cmdvault
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
GRANT USAGE, CREATE ON SCHEMA public TO cmdvault;
-- Restrict pg_hba.conf to the service host/role with scram-sha-256.
-- Use a separate disposable development role with CREATEDB for Django tests.
