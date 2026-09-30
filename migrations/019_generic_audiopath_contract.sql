-- =============================================================================
-- RadioMonitor -- Migration 019: GenericTranscriptionJob.AudioPath contract
-- =============================================================================
-- AudioPath is NOT NULL on the live table (discovered during testing). The
-- web app now populates it at job-insert time with the audio_archive path:
--   D:\RadioMonitor\audio_archive\<FingerprintID>.mp3
-- This migration makes no schema change -- it's a no-op confirmation step,
-- documenting the contract now that both sides agree on AudioPath's purpose.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
GO

PRINT '--- Confirming AudioPath NOT NULL on GenericTranscriptionJob ---';
SELECT COLUMN_NAME, IS_NULLABLE, DATA_TYPE
FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME = 'GenericTranscriptionJob' AND COLUMN_NAME = 'AudioPath';

PRINT '019: no schema change -- web app now populates AudioPath at insert time.';
