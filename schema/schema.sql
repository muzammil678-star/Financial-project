CREATE DATABASE finance_db;
USE finance_db;
CREATE TABLE customers (
  customer_id INT AUTO_INCREMENT PRIMARY KEY,
  customer_code VARCHAR(20) NOT NULL UNIQUE
);

CREATE TABLE accounts (
  account_id INT AUTO_INCREMENT PRIMARY KEY,
  customer_id INT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
);

CREATE TABLE transactions (
  transaction_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  account_id INT NOT NULL,
  transaction_datetime DATETIME NOT NULL,
  amount DECIMAL(12,2) NOT NULL,
  transaction_type VARCHAR(30),
  channel VARCHAR(30),
  region VARCHAR(50),
  is_planted_anomaly TINYINT DEFAULT 0,
  FOREIGN KEY (account_id) REFERENCES accounts(account_id),
  INDEX idx_acct_time (account_id, transaction_datetime)
);

CREATE TABLE rule_config (
  rule_name VARCHAR(50) PRIMARY KEY,
  weight INT NOT NULL,
  threshold DECIMAL(12,2),
  description VARCHAR(255),
  is_active TINYINT DEFAULT 1
);

CREATE TABLE alerts (
  alert_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  transaction_id BIGINT NOT NULL,
  rule_name VARCHAR(50) NOT NULL,
  risk_score DECIMAL(5,2) NOT NULL,
  reason VARCHAR(255),
  review_status ENUM('PENDING','UNDER_REVIEW','CLEARED','ESCALATED') DEFAULT 'PENDING',
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (transaction_id) REFERENCES transactions(transaction_id)
);

CREATE TABLE audit_log (
  log_id BIGINT AUTO_INCREMENT PRIMARY KEY,
  user_name VARCHAR(50),
  action VARCHAR(50),
  entity VARCHAR(50),
  entity_id VARCHAR(50),
  details VARCHAR(255),
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);