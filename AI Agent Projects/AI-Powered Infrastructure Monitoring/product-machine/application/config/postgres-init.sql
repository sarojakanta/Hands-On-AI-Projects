-- PostgreSQL initialization script

-- Create application database
CREATE DATABASE productdb;

-- Connect to database
\c productdb;

-- Create sample tables
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(100) UNIQUE NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reports (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    report_data JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_log (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id),
    action VARCHAR(100),
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Insert sample data
INSERT INTO users (username, email) VALUES
    ('user1', 'user1@example.com'),
    ('user2', 'user2@example.com'),
    ('user3', 'user3@example.com');

-- Create indexes
CREATE INDEX idx_users_email ON users(email);
CREATE INDEX idx_reports_user ON reports(user_id);
CREATE INDEX idx_activity_user ON activity_log(user_id);

-- Grant permissions
GRANT ALL PRIVILEGES ON DATABASE productdb TO postgres;
