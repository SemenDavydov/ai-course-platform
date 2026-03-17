-- Вставляем администратора
INSERT INTO users (username, email, role, is_admin, has_access, is_active, is_blocked, accepted_offer, created_at)
VALUES (
    'admin',
    'sd-a1395@yandex.ru',
    'superadmin',
    true,
    true,
    true,
    false,
    false,
    NOW()
);
