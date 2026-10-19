USE finance_db;
DELETE FROM rule_config;
INSERT INTO rule_config (rule_name, weight, threshold, description) VALUES
('UNUSUAL_AMOUNT', 40, 4.00, 'Amount is 4 or more standard deviations above the account average'),
('HIGH_FREQUENCY', 35, 5.00, '5 or more transactions by the same account within 10 minutes'),
('LOCATION_CHANGE', 35, 0.01, 'Region used in under 1% of account history, between 00:00 and 04:59');