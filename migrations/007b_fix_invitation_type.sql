-- RadioMonitor -- Migration 007b: fix CK_Invitation_Type
-- 007 added SubscriberID to UserInvitation but forgot to update the check
-- constraint (which still expects the old client_admin/agency_admin values).
-- This script drops and recreates it with the new 2-value vocabulary.
USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;
IF EXISTS (SELECT 1 FROM sys.check_constraints WHERE name = 'CK_Invitation_Type')
    ALTER TABLE dbo.UserInvitation DROP CONSTRAINT CK_Invitation_Type;
ALTER TABLE dbo.UserInvitation ADD CONSTRAINT CK_Invitation_Type
    CHECK (UserType IN ('subscriber_admin','subscriber_user'));
COMMIT TRANSACTION;
GO
PRINT '007b applied: CK_Invitation_Type updated.';
