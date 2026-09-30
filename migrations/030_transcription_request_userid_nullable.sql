-- Migration 030: Make TranscriptionRequest.UserID nullable
-- API requests have no logged-in user so UserID must be optional.
USE RadioMonitor;
GO
ALTER TABLE dbo.TranscriptionRequest
    ALTER COLUMN UserID INT NULL;
GO
PRINT '030 applied.';
