-- Autocure suggested index (NEVER auto-applied)
-- Incident: 32
-- Review carefully before applying.

-- No index can efficiently fix ORDER BY RAND().
-- If random sampling is required, consider an application-maintained random key column and index, for example:
-- ALTER TABLE audit_log ADD COLUMN sample_key DOUBLE NOT NULL;
-- CREATE INDEX idx_audit_log_sample_key ON audit_log(sample_key);
-- Then query by sample_key instead of using ORDER BY RAND().;
