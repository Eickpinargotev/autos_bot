SELECT 'CREATE DATABASE evogo_auth'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'evogo_auth')\gexec

SELECT 'CREATE DATABASE evogo_users'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'evogo_users')\gexec
