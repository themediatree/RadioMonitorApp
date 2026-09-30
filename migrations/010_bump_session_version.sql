-- Migration 010: Bump SessionVersion to invalidate all existing sessions.
-- Existing tokens were issued without the session_version claim (or with
-- the old default of 1). Bumping to 2 forces everyone to log in again
-- and receive a properly-versioned token.
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
UPDATE [User] SET SessionVersion = 2 WHERE SessionVersion = 1;
PRINT '010 applied: all sessions invalidated.';
GO
