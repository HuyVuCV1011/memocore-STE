-- Migration: Add project_type and parent_project_id, and backfill legacy projects.

-- 1. Alter projects table to add new taxonomy and hierarchy columns
ALTER TABLE projects ADD COLUMN project_type TEXT;
ALTER TABLE projects ADD COLUMN parent_project_id TEXT REFERENCES projects(id);

-- 2. Insert new parent/structure projects (idempotent INSERT OR IGNORE)
-- STE Portfolio (Ensured to exist first to satisfy FOREIGN KEY constraint for children)
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'e456b1e4-e10d-4925-bc02-61c0de2194f9',
    'STE',
    '[]',
    'Danh mục công việc do Vũ sáng lập và vận hành, gồm quản trị tổng thể, thiết lập công ty, STE Tech/Data/AI, STE Edu, sản phẩm đào tạo, tài liệu bán hàng, định hướng đầu tư và các dự án khách hàng.',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'portfolio',
    NULL
);

-- MindX Portfolio
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'e1f9cb24-2c67-4bb7-bf53-7313a30c5e7b',
    'MindX',
    '[]',
    'MindX Portfolio và vận hành giảng dạy',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'portfolio',
    NULL
);

-- STE - Data & BI Capability (parent: STE)
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'd6b9e598-a6f6-42d7-98c4-c08bf60e2ab4',
    'Data & BI',
    '[]',
    'Năng lực dữ liệu và báo cáo tự động tại STE',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'capability',
    (SELECT id FROM projects WHERE name = 'STE')
);

-- STE - AI & Automation Capability (parent: STE)
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'fa3d17db-bc47-4950-8bfa-267cb56a07cb',
    'AI & Automation',
    '[]',
    'Năng lực AI agent, prompt và chatbot tại STE',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'capability',
    (SELECT id FROM projects WHERE name = 'STE')
);

-- STE - Software & Delivery Capability (parent: STE)
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'cf9c27b0-73f8-45a7-9524-ec1fa91ffc11',
    'Software & Delivery',
    '[]',
    'Mảng outsourcing web/app và dự án khách hàng của STE',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'capability',
    (SELECT id FROM projects WHERE name = 'STE')
);

-- STE - Education & Training Capability (parent: STE)
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'b27c3f30-6db7-458f-b9f4-27ee39b1a511',
    'Education & Training',
    '[]',
    'Sản phẩm đào tạo, dạy học và mở rộng giáo dục tại STE',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'capability',
    (SELECT id FROM projects WHERE name = 'STE')
);

-- STE - Internal Operations Capability (parent: STE)
INSERT OR IGNORE INTO projects (id, name, aliases, summary, status, tags, last_seen_at, created_at, updated_at, project_type, parent_project_id)
VALUES (
    'd5f04bf4-7b1f-47bc-8f43-1e5b871c8c11',
    'Internal Operations',
    '[]',
    'Vận hành nội bộ, pháp lý, công ty và sales assets tại STE',
    'active',
    '[]',
    datetime('now'),
    datetime('now'),
    datetime('now'),
    'capability',
    (SELECT id FROM projects WHERE name = 'STE')
);

-- 3. Backfill legacy projects
-- STE
UPDATE projects SET project_type = 'portfolio', parent_project_id = NULL, status = 'active'
WHERE name = 'STE';

-- MemoCore
UPDATE projects SET project_type = 'independent_project', parent_project_id = NULL, status = 'active'
WHERE name = 'MemoCore';

-- MindX Teaching Operations
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'MindX'), status = 'active'
WHERE name = 'MindX Teaching Operations';

-- MindX Success / SS
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'MindX'), status = 'active'
WHERE name = 'MindX Success / SS';

-- STE - Data & BI Capability and descendants
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'STE'), status = 'active'
WHERE name = 'Data & BI';
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'STE'), status = 'active'
WHERE name = 'AI & Automation';
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'STE'), status = 'active'
WHERE name = 'Software & Delivery';
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'STE'), status = 'active'
WHERE name = 'Education & Training';
UPDATE projects SET project_type = 'capability', parent_project_id = (SELECT id FROM projects WHERE name = 'STE'), status = 'active'
WHERE name = 'Internal Operations';

-- STE STEDATA / Dashboard / Data Systems
UPDATE projects SET project_type = 'product', parent_project_id = (SELECT id FROM projects WHERE name = 'Data & BI'), status = 'active'
WHERE name = 'STE STEDATA / Dashboard / Data Systems';

-- STE Data Analyst / BI Training Product
UPDATE projects SET project_type = 'product', parent_project_id = (SELECT id FROM projects WHERE name = 'Data & BI'), status = 'active'
WHERE name = 'STE Data Analyst / BI Training Product';

-- STE AI Automation / Agent Product-Service Line
UPDATE projects SET project_type = 'product', parent_project_id = (SELECT id FROM projects WHERE name = 'AI & Automation'), status = 'active'
WHERE name = 'STE AI Automation / Agent Product-Service Line';

-- STE Web/App Outsource
UPDATE projects SET project_type = 'product', parent_project_id = (SELECT id FROM projects WHERE name = 'Software & Delivery'), status = 'active'
WHERE name = 'STE Web/App Outsource';

-- STE Lộc / Walmart-style Sourcing Project
UPDATE projects SET project_type = 'client_project', parent_project_id = (SELECT id FROM projects WHERE name = 'Software & Delivery'), status = 'active'
WHERE name = 'STE Lộc / Walmart-style Sourcing Project';

-- STE Edu / Quy Nhơn / Classes
UPDATE projects SET project_type = 'client_project', parent_project_id = (SELECT id FROM projects WHERE name = 'Education & Training'), status = 'active'
WHERE name = 'STE Edu / Quy Nhơn / Classes';

-- STE Teaching / Training Products
UPDATE projects SET project_type = 'product', parent_project_id = (SELECT id FROM projects WHERE name = 'Education & Training'), status = 'active'
WHERE name = 'STE Teaching / Training Products';

-- STE Legal / Company Setup
UPDATE projects SET project_type = 'initiative', parent_project_id = (SELECT id FROM projects WHERE name = 'Internal Operations'), status = 'active'
WHERE name = 'STE Legal / Company Setup';

-- STE Portfolio / Sales Assets
UPDATE projects SET project_type = 'initiative', parent_project_id = (SELECT id FROM projects WHERE name = 'Internal Operations'), status = 'active'
WHERE name = 'STE Portfolio / Sales Assets';

-- Review items (marked as review status, parent NULL)
UPDATE projects SET project_type = 'product', parent_project_id = NULL, status = 'review'
WHERE name = 'STE Student Portfolio / Capstone Product';

UPDATE projects SET project_type = 'product', parent_project_id = NULL, status = 'review'
WHERE name = 'STE Dashboard / PBI Course Or Workshop';

UPDATE projects SET project_type = 'product', parent_project_id = NULL, status = 'review'
WHERE name = 'STE Course Materials / Syllabus / Curriculum';

UPDATE projects SET project_type = 'product', parent_project_id = NULL, status = 'review'
WHERE name = 'STE Excel / Business Data Training';

UPDATE projects SET project_type = 'product', parent_project_id = NULL, status = 'review'
WHERE name = 'STE AI Tool / Agent Training';

-- Incubating items
UPDATE projects SET project_type = 'independent_project', parent_project_id = NULL, status = 'incubating'
WHERE name = 'STE Tài Pet/Gacha/Todo App';

UPDATE projects SET project_type = 'independent_project', parent_project_id = NULL, status = 'incubating'
WHERE name = 'STE Shopee / E-commerce Pricing';

-- Fallback for any other projects that might be added programmatically in tests or unclassified
UPDATE projects SET status = 'review' WHERE project_type IS NULL;

