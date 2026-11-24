ALTER TABLE attempts DROP CONSTRAINT attempts_state_check;
ALTER TABLE attempts ADD CONSTRAINT attempts_state_check CHECK(state IN
 ('queued','preparing','starting','running','cancel_pending','completed','cancelled','blocked','needs_recovery'));
