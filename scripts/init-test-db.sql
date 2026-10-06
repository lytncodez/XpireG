-- Runs once when the PostgreSQL volume is first created (docker-entrypoint-initdb.d).
-- Separate database used by pytest so tests never touch real data.
CREATE DATABASE expireguard_test;
