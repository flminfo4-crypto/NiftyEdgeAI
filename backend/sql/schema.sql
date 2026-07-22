-- NiftyEdge database schema
-- Target: SQL Server Express, local instance (SQLEXPRESS)
-- Mirrors docs/database-design.md exactly. Safe to re-run: guards on IF NOT EXISTS.

IF DB_ID('NiftyEdge') IS NULL
    CREATE DATABASE NiftyEdge;
GO

USE NiftyEdge;
GO

IF SCHEMA_ID('market') IS NULL EXEC('CREATE SCHEMA market');
IF SCHEMA_ID('analytics') IS NULL EXEC('CREATE SCHEMA analytics');
IF SCHEMA_ID('portfolio') IS NULL EXEC('CREATE SCHEMA portfolio');
IF SCHEMA_ID('backtest') IS NULL EXEC('CREATE SCHEMA backtest');
IF SCHEMA_ID('app') IS NULL EXEC('CREATE SCHEMA app');
GO

-- ===================== market =====================

CREATE TABLE market.Instrument (
    SecurityId              INT             NOT NULL,
    Exchange                VARCHAR(10)     NOT NULL,
    Segment                 VARCHAR(10)     NOT NULL,
    InstrumentType          VARCHAR(10)     NOT NULL,
    Symbol                  VARCHAR(30)     NOT NULL,
    DisplayName             NVARCHAR(100)   NOT NULL,
    UnderlyingSecurityId    INT             NULL,
    ExpiryDate              DATE            NULL,
    StrikePrice             DECIMAL(12,4)   NULL,
    OptionType              CHAR(2)         NULL,
    LotSize                 INT             NULL,
    TickSize                DECIMAL(6,4)    NULL,
    IsActive                BIT             NOT NULL DEFAULT 1,
    LastSyncedAt            DATETIME2(0)    NOT NULL,
    CONSTRAINT PK_Instrument PRIMARY KEY CLUSTERED (SecurityId),
    CONSTRAINT FK_Instrument_Underlying FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE market.Expiry (
    UnderlyingSecurityId    INT             NOT NULL,
    ExpiryDate              DATE            NOT NULL,
    ExpiryType              VARCHAR(10)     NOT NULL,
    IsFrontWeek             BIT             NOT NULL DEFAULT 0,
    CONSTRAINT PK_Expiry PRIMARY KEY CLUSTERED (UnderlyingSecurityId, ExpiryDate),
    CONSTRAINT FK_Expiry_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE market.OhlcBar (
    SecurityId          INT             NOT NULL,
    Timeframe           VARCHAR(4)      NOT NULL,
    BarTime             DATETIME2(0)    NOT NULL,
    [Open]              DECIMAL(12,4)   NOT NULL,
    High                DECIMAL(12,4)   NOT NULL,
    Low                 DECIMAL(12,4)   NOT NULL,
    [Close]              DECIMAL(12,4)   NOT NULL,
    Volume              BIGINT          NOT NULL,
    OpenInterest        BIGINT          NULL,
    CumulativeDelta     BIGINT          NULL,
    CONSTRAINT PK_OhlcBar PRIMARY KEY NONCLUSTERED (SecurityId, Timeframe, BarTime),
    CONSTRAINT FK_OhlcBar_Instrument FOREIGN KEY (SecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO
CREATE CLUSTERED COLUMNSTORE INDEX CCI_OhlcBar ON market.OhlcBar;
GO

CREATE TABLE market.OptionChainSnapshot (
    UnderlyingSecurityId    INT             NOT NULL,
    ExpiryDate              DATE            NOT NULL,
    SnapshotTime            DATETIME2(0)    NOT NULL,
    StrikePrice             DECIMAL(12,4)   NOT NULL,
    OptionType              CHAR(2)         NOT NULL,
    Ltp                     DECIMAL(12,4)   NOT NULL,
    BidPrice                DECIMAL(12,4)   NULL,
    AskPrice                DECIMAL(12,4)   NULL,
    Volume                  BIGINT          NOT NULL DEFAULT 0,
    OpenInterest            BIGINT          NOT NULL DEFAULT 0,
    OiChange                BIGINT          NOT NULL DEFAULT 0,
    ImpliedVolatility       DECIMAL(6,3)    NULL,
    Delta                   DECIMAL(10,5)   NULL,
    Gamma                   DECIMAL(10,5)   NULL,
    Theta                   DECIMAL(10,5)   NULL,
    Vega                    DECIMAL(10,5)   NULL,
    Rho                     DECIMAL(10,5)   NULL,
    OiBias                  VARCHAR(6)      NULL,
    CONSTRAINT PK_OptionChainSnapshot PRIMARY KEY NONCLUSTERED
        (UnderlyingSecurityId, ExpiryDate, SnapshotTime, StrikePrice, OptionType),
    CONSTRAINT FK_OptionChainSnapshot_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO
CREATE CLUSTERED COLUMNSTORE INDEX CCI_OptionChainSnapshot ON market.OptionChainSnapshot;
GO
CREATE NONCLUSTERED INDEX IX_OptionChainSnapshot_Latest
    ON market.OptionChainSnapshot (UnderlyingSecurityId, ExpiryDate, SnapshotTime DESC);
GO

CREATE TABLE market.InstrumentQuote (
    SecurityId      INT             NOT NULL,
    Ltp             DECIMAL(12,4)   NOT NULL,
    PrevClose       DECIMAL(12,4)   NULL,
    Change          DECIMAL(10,4)   NULL,
    ChangePct       DECIMAL(10,4)   NULL,
    Volume          BIGINT          NULL,
    OpenInterest    BIGINT          NULL,
    UpdatedAt       DATETIME2(3)    NOT NULL,
    CONSTRAINT PK_InstrumentQuote PRIMARY KEY CLUSTERED (SecurityId),
    CONSTRAINT FK_InstrumentQuote_Instrument FOREIGN KEY (SecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

-- ===================== analytics =====================

CREATE TABLE analytics.MarketBiasSnapshot (
    Id                      BIGINT IDENTITY(1,1) NOT NULL,
    UnderlyingSecurityId    INT             NOT NULL,
    SnapshotTime            DATETIME2(0)    NOT NULL,
    Bias                    VARCHAR(10)     NOT NULL,
    VahLevel                DECIMAL(12,4)   NULL,
    ConfidencePct           DECIMAL(5,2)    NOT NULL,
    PriceVsValue            VARCHAR(20)     NULL,
    OrderFlow               VARCHAR(20)     NULL,
    OiTrend                 VARCHAR(20)     NULL,
    DeltaBias               VARCHAR(10)     NULL,
    GammaBias               VARCHAR(10)     NULL,
    CONSTRAINT PK_MarketBiasSnapshot PRIMARY KEY CLUSTERED (Id),
    CONSTRAINT FK_MarketBiasSnapshot_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE analytics.PriceValueLevels (
    UnderlyingSecurityId    INT             NOT NULL,
    TradeDate               DATE            NOT NULL,
    Vah                     DECIMAL(12,4)   NULL,
    Poc                     DECIMAL(12,4)   NULL,
    Val                     DECIMAL(12,4)   NULL,
    [Pivot]                 DECIMAL(12,4)   NULL,
    R1                      DECIMAL(12,4)   NULL,
    S1                      DECIMAL(12,4)   NULL,
    CurrentPrice            DECIMAL(12,4)   NULL,
    PositionVsVah           VARCHAR(10)     NULL,
    CONSTRAINT PK_PriceValueLevels PRIMARY KEY CLUSTERED (UnderlyingSecurityId, TradeDate),
    CONSTRAINT FK_PriceValueLevels_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE analytics.VolumeProfileBin (
    UnderlyingSecurityId    INT             NOT NULL,
    TradeDate               DATE            NOT NULL,
    PriceBinLow             DECIMAL(12,4)   NOT NULL,
    PriceBinHigh            DECIMAL(12,4)   NOT NULL,
    Volume                  BIGINT          NOT NULL,
    IsPoc                   BIT             NOT NULL DEFAULT 0,
    CONSTRAINT PK_VolumeProfileBin PRIMARY KEY CLUSTERED (UnderlyingSecurityId, TradeDate, PriceBinLow),
    CONSTRAINT FK_VolumeProfileBin_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE analytics.MarketInternalsSnapshot (
    Id                      BIGINT IDENTITY(1,1) NOT NULL,
    UnderlyingSecurityId    INT             NOT NULL,
    SnapshotTime            DATETIME2(0)    NOT NULL,
    Advances                INT             NULL,
    Declines                INT             NULL,
    AdRatio                 DECIMAL(6,3)    NULL,
    NewHigh                 INT             NULL,
    NewLow                  INT             NULL,
    CumulativeDelta         BIGINT          NULL,
    StraddlePremium         DECIMAL(12,4)   NULL,
    AtmIv                   DECIMAL(6,3)    NULL,
    IvRank                  DECIMAL(5,2)    NULL,
    IvPercentile            DECIMAL(5,2)    NULL,
    PcrOi                   DECIMAL(6,3)    NULL,
    CONSTRAINT PK_MarketInternalsSnapshot PRIMARY KEY CLUSTERED (Id),
    CONSTRAINT FK_MarketInternalsSnapshot_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE analytics.StrategySignal (
    SignalId                BIGINT IDENTITY(1,1) NOT NULL,
    UnderlyingSecurityId    INT             NOT NULL,
    ExpiryDate              DATE            NOT NULL,
    GeneratedAt             DATETIME2(0)    NOT NULL,
    SignalType              VARCHAR(20)     NOT NULL,
    IsPrimary               BIT             NOT NULL,
    ConfidencePct           DECIMAL(5,2)    NOT NULL,
    TriggerCondition        NVARCHAR(100)   NULL,
    EntryZoneLow            DECIMAL(12,4)   NULL,
    EntryZoneHigh           DECIMAL(12,4)   NULL,
    Target                  DECIMAL(12,4)   NULL,
    StopLoss                DECIMAL(12,4)   NULL,
    ReasonsJson             NVARCHAR(MAX)   NULL,
    Status                  VARCHAR(12)     NOT NULL DEFAULT 'ACTIVE',
    ClosedAt                DATETIME2(0)    NULL,
    CONSTRAINT PK_StrategySignal PRIMARY KEY CLUSTERED (SignalId),
    CONSTRAINT FK_StrategySignal_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId),
    CONSTRAINT CK_StrategySignal_ReasonsJson CHECK (ReasonsJson IS NULL OR ISJSON(ReasonsJson) = 1)
);
GO

-- ===================== portfolio =====================

CREATE TABLE portfolio.Position (
    PositionId      BIGINT IDENTITY(1,1) NOT NULL,
    SecurityId      INT             NOT NULL,
    Quantity        INT             NOT NULL,
    AvgPrice        DECIMAL(12,4)   NOT NULL,
    Pnl             DECIMAL(14,2)   NULL,
    PnlPct          DECIMAL(8,4)    NULL,
    Status          VARCHAR(10)     NOT NULL DEFAULT 'OPEN',
    OpenedAt        DATETIME2(0)    NOT NULL,
    ClosedAt        DATETIME2(0)    NULL,
    SyncedAt        DATETIME2(0)    NOT NULL,
    CONSTRAINT PK_Position PRIMARY KEY CLUSTERED (PositionId),
    CONSTRAINT FK_Position_Instrument FOREIGN KEY (SecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE portfolio.[Order] (
    BrokerOrderId   VARCHAR(30)     NOT NULL,
    SecurityId      INT             NOT NULL,
    Side            VARCHAR(4)      NOT NULL,
    OrderType       VARCHAR(10)     NOT NULL,
    Quantity        INT             NOT NULL,
    Price           DECIMAL(12,4)   NULL,
    Status          VARCHAR(12)     NOT NULL,
    PlacedAt        DATETIME2(0)    NOT NULL,
    UpdatedAt       DATETIME2(0)    NOT NULL,
    CONSTRAINT PK_Order PRIMARY KEY CLUSTERED (BrokerOrderId),
    CONSTRAINT FK_Order_Instrument FOREIGN KEY (SecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

CREATE TABLE portfolio.RiskSnapshot (
    SnapshotTime        DATETIME2(0)    NOT NULL,
    NetDelta            DECIMAL(10,4)   NULL,
    NetGamma            DECIMAL(10,4)   NULL,
    NetTheta            DECIMAL(12,4)   NULL,
    NetVega             DECIMAL(12,4)   NULL,
    MarginUsed          DECIMAL(14,2)   NULL,
    MarginAvailable     DECIMAL(14,2)   NULL,
    ExposurePct         DECIMAL(5,2)    NULL,
    PnlToday            DECIMAL(14,2)   NULL,
    CONSTRAINT PK_RiskSnapshot PRIMARY KEY CLUSTERED (SnapshotTime)
);
GO

-- ===================== backtest =====================

CREATE TABLE backtest.BacktestRun (
    BacktestRunId           BIGINT IDENTITY(1,1) NOT NULL,
    StrategyName            VARCHAR(50)     NOT NULL,
    ParamsJson              NVARCHAR(MAX)   NULL,
    UnderlyingSecurityId    INT             NOT NULL,
    StartDate               DATE            NOT NULL,
    EndDate                 DATE            NOT NULL,
    CreatedAt               DATETIME2(0)    NOT NULL,
    Status                  VARCHAR(12)     NOT NULL DEFAULT 'RUNNING',
    TotalTrades             INT             NULL,
    WinRate                 DECIMAL(5,2)    NULL,
    TotalPnl                DECIMAL(14,2)   NULL,
    MaxDrawdown             DECIMAL(14,2)   NULL,
    SharpeRatio             DECIMAL(6,3)    NULL,
    CONSTRAINT PK_BacktestRun PRIMARY KEY CLUSTERED (BacktestRunId),
    CONSTRAINT FK_BacktestRun_Instrument FOREIGN KEY (UnderlyingSecurityId)
        REFERENCES market.Instrument (SecurityId),
    CONSTRAINT CK_BacktestRun_ParamsJson CHECK (ParamsJson IS NULL OR ISJSON(ParamsJson) = 1)
);
GO

CREATE TABLE backtest.BacktestTrade (
    BacktestTradeId     BIGINT IDENTITY(1,1) NOT NULL,
    BacktestRunId       BIGINT          NOT NULL,
    SecurityId          INT             NOT NULL,
    Side                VARCHAR(4)      NOT NULL,
    EntryTime           DATETIME2(0)    NOT NULL,
    ExitTime            DATETIME2(0)    NULL,
    EntryPrice          DECIMAL(12,4)   NOT NULL,
    ExitPrice           DECIMAL(12,4)   NULL,
    Quantity            INT             NOT NULL,
    Pnl                 DECIMAL(14,2)   NULL,
    ExitReason          VARCHAR(10)     NULL,
    CONSTRAINT PK_BacktestTrade PRIMARY KEY CLUSTERED (BacktestTradeId),
    CONSTRAINT FK_BacktestTrade_Run FOREIGN KEY (BacktestRunId)
        REFERENCES backtest.BacktestRun (BacktestRunId),
    CONSTRAINT FK_BacktestTrade_Instrument FOREIGN KEY (SecurityId)
        REFERENCES market.Instrument (SecurityId)
);
GO

-- ===================== app =====================

CREATE TABLE app.AppSetting (
    SettingKey      VARCHAR(50)     NOT NULL,
    SettingValue    NVARCHAR(200)   NULL,
    UpdatedAt       DATETIME2(0)    NOT NULL,
    CONSTRAINT PK_AppSetting PRIMARY KEY CLUSTERED (SettingKey)
);
GO
