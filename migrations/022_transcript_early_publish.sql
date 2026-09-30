-- =============================================================================
-- RadioMonitor -- Migration 022: Transcript early-publish path columns
-- =============================================================================
-- EarlyJsonPath/EarlyTextPath: fast-lane copy written by the pipeline's
-- live transcriber immediately at publish time, independent of
-- final_mover.py's full pipeline move. JsonPath/TextPath remain
-- AUDIOPROC-local (C:\...) until that move completes.
--
-- IMPORTANT (confirmed with pipeline): these columns do NOT follow a fixed
-- folder shape. They may or may not include an '\archives\' segment
-- depending on whether audio_extractor.py's local archiving step ran
-- before or after early-publish fired for a given chunk. The web app reads
-- the column value as-is and checks it on disk directly -- it never
-- reconstructs the path from a template.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.Transcript') AND name='EarlyJsonPath')
    ALTER TABLE dbo.Transcript ADD EarlyJsonPath NVARCHAR(500) NULL;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.Transcript') AND name='EarlyTextPath')
    ALTER TABLE dbo.Transcript ADD EarlyTextPath NVARCHAR(500) NULL;

COMMIT TRANSACTION;
GO

PRINT '--- Transcript early-publish columns ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='Transcript' AND COLUMN_NAME IN ('EarlyJsonPath','EarlyTextPath');

PRINT '022 applied.';
