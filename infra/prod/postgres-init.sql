-- Runs once, on first start of the production postgres container.
CREATE DATABASE keycloak;

\connect assistant
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;
