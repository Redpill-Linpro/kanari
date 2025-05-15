-- Create a "kanari" table and insert dummy data, so that the application has something to retrieve

CREATE TABLE kanari (
    id INT AUTO_INCREMENT PRIMARY KEY,
    a INT NOT NULL,
    b INT NOT NULL,
    result INT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO kanari (a, b, result) VALUES
    (12, 34, 408),
    (56, 78, 4368),
    (100, 200, 20000);
