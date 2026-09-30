-- =============================================================================
-- RadioMonitor -- Migration 023: Per-user station permissions
-- =============================================================================
-- Subscriber_admins restrict subscriber_users to specific stations and/or
-- revoke download/report access, within their own Subscriber only.
-- Internal staff never set these -- only the tenant's own admin can.
--
-- Grandfathering: existing users default to UNRESTRICTED (full access to
-- everything their Subscriber has) via HasStationRestrictions=0. The
-- moment an admin saves ANY station selection for a user (even "none"),
-- HasStationRestrictions flips to 1 and UserStationPermission rows become
-- the source of truth. New users created going forward also default to
-- unrestricted until an admin actively configures them -- this matches
-- "nothing until explicitly granted" being interpreted as "no explicit
-- restriction" rather than "locked out by default", since a brand new
-- invited user should be usable immediately without extra admin steps,
-- and the admin opts IN to restricting rather than opting OUT of a default
-- lockout. (Confirmed acceptable: restriction is admin-initiated, not a
-- default-deny security boundary against untrusted parties -- the user is
-- already inside the Subscriber's tenant.)
-- =============================================================================

USE RadioMonitor;
GO
SET QUOTED_IDENTIFIER ON;
SET XACT_ABORT ON;
GO
BEGIN TRANSACTION;

-- -----------------------------------------------------------------------------
-- User.CanDownload -- download/report/export access. Default TRUE so
-- existing users are unaffected; admin can revoke per user.
-- -----------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.[User]') AND name='CanDownload')
    ALTER TABLE dbo.[User] ADD CanDownload BIT NOT NULL
        CONSTRAINT DF_User_CanDownload DEFAULT 1;

-- -----------------------------------------------------------------------------
-- User.HasStationRestrictions -- FALSE means unrestricted (sees everything
-- the Subscriber has, ignoring UserStationPermission entirely). TRUE means
-- "use UserStationPermission as the explicit allow-list, possibly empty."
-- -----------------------------------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.columns
    WHERE object_id=OBJECT_ID('dbo.[User]') AND name='HasStationRestrictions')
    ALTER TABLE dbo.[User] ADD HasStationRestrictions BIT NOT NULL
        CONSTRAINT DF_User_HasStationRestrictions DEFAULT 0;

-- -----------------------------------------------------------------------------
-- UserStationPermission -- explicit allow-list, only consulted when
-- HasStationRestrictions=1 on the owning User.
-- -----------------------------------------------------------------------------
IF OBJECT_ID('dbo.UserStationPermission','U') IS NULL
BEGIN
    CREATE TABLE dbo.UserStationPermission (
        UserID      INT NOT NULL,
        StationID   INT NOT NULL,
        CreatedAt   DATETIME2 NOT NULL CONSTRAINT DF_USP_CreatedAt DEFAULT SYSDATETIME(),

        CONSTRAINT PK_UserStationPermission PRIMARY KEY (UserID, StationID),
        CONSTRAINT FK_USP_User FOREIGN KEY (UserID) REFERENCES dbo.[User](UserID),
        CONSTRAINT FK_USP_Station FOREIGN KEY (StationID) REFERENCES dbo.Station(StationID)
    );
END

COMMIT TRANSACTION;
GO

PRINT '--- User new columns ---';
SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE FROM INFORMATION_SCHEMA.COLUMNS
WHERE TABLE_NAME='User' AND COLUMN_NAME IN ('CanDownload','HasStationRestrictions');

PRINT '--- UserStationPermission ---';
SELECT name FROM sys.tables WHERE name = 'UserStationPermission';

PRINT '023 applied.';
