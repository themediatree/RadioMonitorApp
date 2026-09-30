-- Samsung complete cleanup for retest
-- Removes all Commercial, Campaign, CampaignCommercial, CampaignStation rows
-- associated with any Samsung commercial. Safe to run multiple times.
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON; SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

-- Find all Samsung commercial IDs
DECLARE @samsung_ids TABLE (CommercialID INT);
INSERT INTO @samsung_ids
    SELECT CommercialID FROM Commercial
    WHERE DisplayTapeID LIKE '%SAMSUNG%' OR CommercialName LIKE '%SAMSUNG%';

-- Find all campaign IDs that only contain Samsung commercials
-- (campaigns linked exclusively to Samsung rows)
DECLARE @samsung_campaign_ids TABLE (CampaignID INT);
INSERT INTO @samsung_campaign_ids
    SELECT DISTINCT cc.CampaignID FROM CampaignCommercial cc
    WHERE cc.CommercialID IN (SELECT CommercialID FROM @samsung_ids);

-- Remove detections linked to Samsung commercials
DELETE FROM Detection
    WHERE CommercialID IN (SELECT CommercialID FROM @samsung_ids);

-- Remove campaign links
DELETE FROM CampaignCommercial
    WHERE CommercialID IN (SELECT CommercialID FROM @samsung_ids);

-- Remove station links for Samsung campaigns
DELETE FROM CampaignStation
    WHERE CampaignID IN (SELECT CampaignID FROM @samsung_campaign_ids);

-- Remove campaigns that are now empty
DELETE FROM Campaign
    WHERE CampaignID IN (SELECT CampaignID FROM @samsung_campaign_ids)
    AND CampaignID NOT IN (SELECT CampaignID FROM CampaignCommercial);

-- Remove Samsung commercials
DELETE FROM Commercial
    WHERE CommercialID IN (SELECT CommercialID FROM @samsung_ids);

COMMIT TRANSACTION;
GO

-- Verify
PRINT '--- Samsung rows remaining (should be 0) ---';
SELECT COUNT(*) AS SamsungRowsRemaining FROM Commercial
WHERE DisplayTapeID LIKE '%SAMSUNG%' OR CommercialName LIKE '%SAMSUNG%';
