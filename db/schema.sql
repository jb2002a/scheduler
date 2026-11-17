PRAGMA foreign_keys = ON;

CREATE TABLE courses (
    id INTEGER PRIMARY KEY,
    semester INTEGER NOT NULL CHECK (semester IN (1, 2)),
    course_code TEXT NOT NULL,
    category TEXT NOT NULL,
    category_group TEXT NOT NULL,
    name TEXT NOT NULL,
    name_en TEXT,
    credits INTEGER NOT NULL,
    theory INTEGER,
    practice INTEGER,
    raw_time TEXT NOT NULL,
    schedulable INTEGER NOT NULL CHECK (schedulable IN (0, 1)),
    year INTEGER,
    area TEXT,
    department TEXT,
    UNIQUE (course_code, semester)
);

CREATE TABLE course_slots (
    id INTEGER PRIMARY KEY,
    course_id INTEGER NOT NULL REFERENCES courses (id),
    day TEXT NOT NULL CHECK (day IN ('MON', 'TUE', 'WED', 'THU', 'FRI', 'SAT', 'SUN')),
    period INTEGER NOT NULL CHECK (period BETWEEN 0 AND 10),
    UNIQUE (course_id, day, period)
);

CREATE INDEX idx_courses_semester ON courses (semester);
CREATE INDEX idx_course_slots_course ON course_slots (course_id);
