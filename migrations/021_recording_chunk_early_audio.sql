-- =============================================================================
-- RadioMonitor -- Migration 021: Early audio path for My Transcriptions playback
-- =============================================================================
-- EarlyAudioPath: populated by audio_transcriber.py within seconds of
-- transcription succeeding -- a "fast lane" copy of the chunk audio to
-- AUDIOREC, independent of final_mover.py's full pipeline move (which can
-- take up to 7 days and is gated on fingerprint/song/word processing).
--
-- AudioPath remains the eventual authoritative path once the full pipeline
-- completes. EarlyAudioPath may be NULL (not yet available -- not an error,
-- audio will arrive later via the normal path) or point to a working copy.
--
-- Confirmed with pipeline thread -- see Word & Phrase / My Transcriptions
-- karaoke design discussion, June 2026.
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.RecordingChunk') AND name='EarlyAudioPath')
    ALTER TABLE dbo.RecordingChunk ADD EarlyAudioPath NVARCHAR(500) NULL;

COMMIT TRANSACTION;
GO

PRINT '--- RecordingChunk.EarlyAudioPath ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='RecordingChunk' AND COLUMN_NAME='EarlyAudioPath';

PRINT '021 applied.';
