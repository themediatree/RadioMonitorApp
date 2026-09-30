-- =============================================================================
-- RadioMonitor -- Migration 016: Liveread karaoke transcript support
-- =============================================================================
-- WordTimestampPath: path to the word-level timestamp JSON for a liveread
-- airing, transcribed fresh per-detection by the pipeline from the actual
-- aired audio chunk. NULL for generic detections (generic uses a separate
-- FingerprintID-keyed lookup -- see GenericTranscript table, migration TBD).
--
-- Path convention (pipeline-confirmed):
--   \\AUDIOREC\RadioMonitor\clips\<station>\<date>\commercials\liveread\<clipname>.json
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.Detection') AND name='WordTimestampPath')
    ALTER TABLE dbo.Detection ADD WordTimestampPath NVARCHAR(500) NULL;

COMMIT TRANSACTION;
GO

PRINT '--- Detection.WordTimestampPath ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='Detection' AND COLUMN_NAME='WordTimestampPath';

PRINT '016 applied.';
