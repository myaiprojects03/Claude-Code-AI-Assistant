-- Runs once, on first start of the postgres container.
CREATE DATABASE keycloak;
CREATE DATABASE assistant_test;

\connect assistant
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;

\connect assistant_test
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
