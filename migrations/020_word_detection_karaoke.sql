-- =============================================================================
-- RadioMonitor -- Migration 020: Word & Phrase karaoke transcript support
-- =============================================================================
-- WordTimestampPath: path to the word-level timestamp JSON for the extended
-- context window around a keyword match (keyword +/- 5s, PRE_SEC/POST_SEC).
-- Written synchronously by the pipeline at detection time -- no job queue,
-- same mechanism as liveread's Detection.WordTimestampPath.
--
-- Path convention (pipeline-confirmed):
--   clips\<station>\<date>\words\<clipname>.json (same basename as ClipPath)
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.WordDetection') AND name='WordTimestampPath')
    ALTER TABLE dbo.WordDetection ADD WordTimestampPath NVARCHAR(500) NULL;

COMMIT TRANSACTION;
GO

PRINT '--- WordDetection.WordTimestampPath ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='WordDetection' AND COLUMN_NAME='WordTimestampPath';

PRINT '020 applied.';
