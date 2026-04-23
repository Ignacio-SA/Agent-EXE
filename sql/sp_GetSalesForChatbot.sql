DROP PROCEDURE IF EXISTS [dbo].[sp_GetSalesForChatbot]
GO

CREATE PROCEDURE [dbo].[sp_GetSalesForChatbot]
    @FranchiseCode NVARCHAR(100),
    @Year          INT      = NULL,
    @DateFrom      DATETIME = NULL,
    @DateTo        DATETIME = NULL
AS
BEGIN
    IF @Year IS NULL AND @DateFrom IS NULL AND @DateTo IS NULL
        SET @Year = YEAR(DATEADD(hour, -3, GETUTCDATE()))

    SELECT h.id, h.FranchiseeCode, h.ShiftCode, h.PosCode,
           h.UserName,
           SWITCHOFFSET(TRY_CONVERT(DATETIMEOFFSET, d.SaleDateTimeUtc), '-03:00') AS SaleDateTimeUtc,
           d.Quantity,
           d.ArticleId, d.ArticleDescription, d.TypeDetail, d.UnitPriceFix,
           d.Type
    FROM [LH_Silver_Cloud_PRO].[dbo].[dt_silver_ingested_cosmos_sales_header] h
    JOIN [LH_Silver_Cloud_PRO].[dbo].[vw_Silver_Cloud_NewDetails] d ON h.id = d.SaleDocId
    WHERE h.FranchiseCode = @FranchiseCode
      AND (
            (@DateFrom IS NULL AND @DateTo IS NULL
             AND YEAR(SWITCHOFFSET(TRY_CONVERT(DATETIMEOFFSET, d.SaleDateTimeUtc), '-03:00')) = @Year)
            OR
            (@DateFrom IS NOT NULL
             AND SWITCHOFFSET(TRY_CONVERT(DATETIMEOFFSET, d.SaleDateTimeUtc), '-03:00') >= @DateFrom
             AND (@DateTo IS NULL OR SWITCHOFFSET(TRY_CONVERT(DATETIMEOFFSET, d.SaleDateTimeUtc), '-03:00') <= @DateTo))
          )
    ORDER BY SaleDateTimeUtc DESC
END
