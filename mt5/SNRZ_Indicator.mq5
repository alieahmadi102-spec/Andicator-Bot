//+------------------------------------------------------------------+
//|                                               SNRZ_Indicator.mq5 |
//|        SNRZ Strategy — "Zindan The Gold Chaser" Masterclass      |
//|                                                                  |
//|  Implements on MetaTrader 5:                                     |
//|   • Valid Support / Valid Resistance (two-movement rule)         |
//|   • 75% breakout rule (body + directional shadow only)           |
//|   • Inversion: RBS / SBR (fresh) , I.VR / I.VS (valid) zones     |
//|   • SRR / RSS (support / resistance that broke >= 2 opposites)   |
//|   • PO2 — 2nd retest of an INVERSION zone (strongest entry)      |
//|   • SNRZ engulfing / pin-bar confirmation                        |
//|   • TWO zone sets at once (book p.41/p.44): the chart's own zones |
//|     give TP1, the analysis timeframe's give the trend and TP2     |
//|   • One position at a time with SL / TP1 / TP2 / TP3 drawn       |
//+------------------------------------------------------------------+
#property copyright   "SNRZ (Zindan The Gold Chaser) — indicator port"
#property version     "12.00"
#property description "SNRZ: chart zones AND analysis zones together (book p.41/p.44), one trade at a time"
#property indicator_chart_window
#property indicator_buffers 4
#property indicator_plots   4

// BUY arrow
#property indicator_label1  "SNRZ Buy"
#property indicator_type1   DRAW_ARROW
#property indicator_color1  clrMediumSeaGreen
#property indicator_width1  2
// SELL arrow
#property indicator_label2  "SNRZ Sell"
#property indicator_type2   DRAW_ARROW
#property indicator_color2  clrTomato
#property indicator_width2  2
// PO2 BUY arrow
#property indicator_label3  "SNRZ PO2 Buy"
#property indicator_type3   DRAW_ARROW
#property indicator_color3  clrLime
#property indicator_width3  3
// PO2 SELL arrow
#property indicator_label4  "SNRZ PO2 Sell"
#property indicator_type4   DRAW_ARROW
#property indicator_color4  clrRed
#property indicator_width4  3

//--- inputs -----------------------------------------------------------------
input group "Zones (SNRZ)"
input ENUM_TIMEFRAMES InpZoneTF = PERIOD_CURRENT; // Zone timeframe (CURRENT = AUTO from book table)
input int    InpPivotLtf     = 5;     // Pivot length — chart zones (smaller = more)
input int    InpPivotHtf     = 8;     // Pivot length — analysis zones
input int    InpMaxZonesLtf  = 14;     // Max chart zones
input int    InpMaxZonesHtf  = 8;     // Max analysis zones
input double InpBigMoveATR   = 1.2;   // "Big Movement" >= ATR x
// Image 36: "the difference from an ordinary support is only that it has a
// First Movement and a Second Movement - and if EACH of the movements is a
// BIG MOVEMENT, the market respects that zone MORE." The pairing path never
// checked this, so zones were born VALID off swings the market barely reacted to.
enum ENUM_BIGMOVE { BIGMOVE_EITHER, BIGMOVE_BOTH, BIGMOVE_OFF };
input ENUM_BIGMOVE InpBigMoveRule = BIGMOVE_EITHER; // Big movement: either one / both / never reject
input bool   InpNeedBigMove  = true;  // A paired zone must have a big movement too
input double InpBreakoutPct  = 75.0;  // Breakout rule (%) — the 75% rule
input double InpMinZoneATR   = 0.15;  // Min zone height (ATR x)
input double InpMaxZoneATR   = 0.40;  // Max zone height (ATR x)
input int    InpLifeLtf      = 600;   // Chart zone lifetime (chart bars)
input int    InpLifeHtf      = 150;    // Analysis zone lifetime (analysis bars)
input double InpMaxZoneDistATR = 6.0; // Hide zones further than (ATR x) - still targets
input bool   InpDropFar      = false; // ...delete them instead of hiding them
input bool   InpExpireByAge  = false; // Let a zone expire by age as well as by a break
input bool   InpPairZones     = true;  // Draw a zone only from TWO swings (p24: S+S/R+R/S+R/R+S)
input double InpPairTolATR   = 0.50;  // ..."similar price" = closer than (ATR x)
input int    InpPairMaxGap   = 150;   // ...and the two swings closer than N bars
input int    InpPairLookback = 20;    // ...searching the last N swings
input bool   InpStructZone   = true;  // Draw the analysis zone from the STRUCTURE of the turn
input int    InpStructBars   = 3;     // ...how many candles either side of the pivot count as the turn
input double InpMaxZoneATRHtf= 1.0;   // Analysis-zone height cap (ATR x) - a sanity bound only
input bool   InpRefineHtf    = true;   // On the chart, redraw the analysis zone from the chart candles (captain's rule)
input bool   InpLineZones    = true;  // Draw LINE-CHART zones too (images 31/12/13)
input bool   InpFlipNeedsPullback = true; // A broken level trades at the PULLBACK swing, not the old band
input bool   InpLineSingleLevels = true; // On a line chart every peak is an R and every trough an S
input bool   InpEngulfFullCandle = true; // The engulf zone is the WHOLE candle: body + wick
input bool   InpEngulfZones  = true;  // Method 5: draw a zone from the ENGULF pair (images 27/28)
input bool   InpMomentumZones= true;  // A single MOMENTUM candle is a zone too (image 43)
input double InpMomBodyATR   = 0.8;   // ..."momentum" = body at least this many ATR
input bool   InpMomFullCandle= true;  // ...and the zone is the WHOLE candle, body with both wicks (image 43)
input bool   InpShowFresh    = false; // Also draw zones that are not valid yet
input bool   InpDropDead     = true;  // Delete a finished zone from the chart
input int    InpNewBars      = 30;    // Mark a zone NEW for this many bars
// Images 31/33/52 - the GAP: "the space between a support and a resistance;
// when that space is created the market fills it back with 80% probability".
// Unlike the S+S / R+R pairs these are two levels at DIFFERENT prices and the
// zone is the band between them. The book marks GAP on H1/H4/Daily/Weekly.
input bool   InpGapZones     = true;  // GAP zones (the band between an S and an R)
input double InpGapMinATR    = 0.6;   // ...the two levels at least this far apart (ATR x)
input double InpGapMaxATR    = 2.5;   // ...and at most this far
input bool   InpGapHtfOnly   = true;  // GAP only on the analysis timeframe
input int    InpFbaBars      = 3;     // A break must hold N bars before the zone inverts (p47)

input group "Signals"
input bool   InpTrendFilter  = true;  // Trade only with structure trend
input bool   InpAllowCounterInv = false; // Allow counter-trend entries on inversion zones
input int    InpMaxTouches   = 3;     // Max touches per zone (3-touch rule)
input int    InpMaxFlips     = 2;     // Max role flips before a zone is finished
input bool   InpKillOnStop   = true;  // A zone that got stopped out is finished
// Master class image 41, the fully worked trade: zone 4706-4720, stop 4698.67
// and a RED LINE at 4732.33 - exactly 1:1 against that stop. "On the 5-minute
// we go break-even and take money off the account, and we wait for the
// target." So break-even happens at 1R, NOT when the first zone target is
// reached: that zone can be far away and the trade would ride all the way
// back to the stop before it was ever protected.
input bool   InpBreakEven    = true;  // Move stop to entry at the 1:1 line
input double InpBeAtR        = 1.0;   // ...meaning this many times the stop distance
input bool   InpSingleTf     = true;  // Everything on THIS timeframe (no separate analysis TF)
// The captain's ladder marks the zones two rungs up and drops to the chart to
// confirm and enter. This turns that off: the chart marks its own zones,
// confirms on itself and opens the trade on itself - 1-minute zones traded on
// the 1-minute chart. With no analysis timeframe the trend filter reads the
// CHART's own structure and TP2 falls back to the next chart zone. Nothing
// else in the rules changes.
input int    InpRangeBars    = 10;    // Range lockout (analysis-TF bars since opposite BOS)
input int    InpMaxOpen      = 3;     // How many zones may carry a live order/trade at once
input double InpMinRR        = 1.0;   // The next zone must be at least this many R away
input int    InpMaxTradeBars = 60;   // Close an open trade after N chart bars
input double InpMinSlATR     = 0.0;   // Minimum stop distance (ATR x) - 0 = the WICK decides, nothing widens it
input double InpTpMaxR       = 6.0;   // Max R for TP1/TP2 (farther zone -> TP3)
input bool   InpEntryAtZone   = true;  // Entry = LIMIT order on the zone (p41/p42)
input bool   InpEntryEdge     = true;  // ...at the near EDGE of the zone, not its middle
input double InpSlBufferATR   = 0.8;   // How far beyond the zone the stop sits (ATR x)
input double InpMaxSlATR      = 3.0;   // Maximum stop distance (ATR x) - 0 = no ceiling.
// The stop sits behind the WICK, but the wick is read from the last 3 bars,
// and when price spiked far below the zone and closed back inside (a false
// break, which the book calls a good sign) that wick can sit ten zone-heights
// away. Measured on M5 the stop is a median 5.9x the zone height, p90 11.7x,
// worst 52x. Every ceiling from 5 ATR down to 2 beat no ceiling at all, which
// is what makes it a real effect and not a fitted number.
input int    InpOrderExpiry   = 10;    // An unfilled limit order dies after N bars
input bool   InpRequireNested = false; // Only chart zones sitting inside an analysis zone (p14)
input bool   InpEntriesHtfOnly= false; // Entries only from analysis-timeframe zones
input double InpRrTp1         = 1.0;   // TP1 = this many times the stop distance (book p41: 1:1)
// Book, liquidity section: "in gold the sell-side liquidity is usually taken
// first and THEN the real move - about 80% of the time". A sell placed right
// after price has just swept a multi-week low is selling the reversal itself.
input bool   InpSweepGuard    = true;  // Do not trade against a fresh liquidity sweep
input int    InpSweepBars     = 40;    // ..."a fresh extreme" = the low/high of N bars
input int    InpSweepRecent   = 10;    // ...and it was made within the last N bars
input bool   InpShowPosition = true;  // Draw Entry / SL / TP1-3 of the last setup

input group "Alerts"
input bool   InpAlertPopup   = true;  // Alert window
input bool   InpAlertPush    = false; // Push notification to phone

input group "Style"
input color  InpColSup       = C'8,153,129';   // Support zone
input color  InpColRes       = C'242,54,69';   // Resistance zone
input color  InpColInv       = C'212,175,55';  // Inversion zone (Zindan gold)
input uchar  InpFillAlpha    = 40;             // (reserved)

//--- buffers ----------------------------------------------------------------
double BufBuy[], BufSell[], BufPO2Buy[], BufPO2Sell[];

//--- zone storage -----------------------------------------------------------
struct SZone
  {
   double   top;
   double   bot;
   int      role;      // 1 = Support, -1 = Resistance
   int      state;     // 0 fresh, 1 VALID, 2 inverted
   int      touches;
   int      oppBreaks; // opposite zones broken since creation (SRR/RSS)
   bool     srr;       // qualified as SRR (support) / RSS (resistance)
   bool     wasValid;
   bool     dead;      // 3-touch rule exhausted -> no more trades
   int      flips;     // role inversions — a level broken from both sides
                       // repeatedly is range noise, not a zone
   bool     htf;       // true = analysis-timeframe zone (book: the TP2 zone)
   int      sigTouch;  // anti-spam latch: one signal per touch
   int      bornH;     // analysis-TF index it was born on
   datetime bornTime;  // pivot time — where the rectangle starts
   datetime activeFrom;// no touches counted before this time
   long     id;        // object id
   bool     inZonePrev;
   bool     paired;    // drawn from TWO swings (p24) — born valid, needs 1 touch
   bool     far;       // too far from price to draw — still a valid target
   int      awaitPull; // broke on this bar, waiting for the pullback swing
   double   rawTop;    // the band as the ANALYSIS timeframe drew it, kept so
   double   rawBot;    // the chart can still outline it once it is refined
   bool     refined;
   int      falseBreaks; // how many times broken and respected again
   bool     fba;       // TWO of those = a False Breakout Area
   int      pendBar;   // chart bar an unconfirmed 75% break happened on
   int      pendDir;   // +1 broke up · -1 broke down · 0 nothing pending
  };
SZone  g_zones[];
long   g_zoneSeq = 0;

int    g_atrHandle    = INVALID_HANDLE;   // chart-TF ATR
int    g_atrHtfHandle = INVALID_HANDLE;   // analysis-TF ATR
ENUM_TIMEFRAMES g_atf = PERIOD_CURRENT;
string g_prefix       = "SNRZ_";

//--- structure trend (analysis timeframe) -----------------------------------
double g_lastHigh = 0, g_prevHigh = 0, g_lastLow = 0, g_prevLow = 0;
int    g_trendState = 0;                  // 1 up · -1 down · 0 undecided
int    g_lastBosUpH = -999999, g_lastBosDnH = -999999;
bool   g_bosUpPrev = false, g_bosDnPrev = false;
// chart-structure fallback for when the analysis timeframe has no opinion yet
double g_cHigh = 0, g_cPrevHigh = 0, g_cLow = 0, g_cPrevLow = 0;

//--- active position --------------------------------------------------------
//--- Orders and trades: ONE PER ZONE ----------------------------------------
// The book stacks zones up the chart and puts an order on each, and each order
// aims at the NEXT zone. A single-slot engine could never do that.
// stat: -9 resting limit - 0 running - 1/2/3 TP hit - -1 SL - -2 BE - -3 timed out
struct SOrd
  {
   bool     buy;
   bool     po2;
   long     uid;
   int      bar;
   datetime time;
   double   entry, sl, tp1, tp2, tp3, risk0;
   string   zone;
   int      stat;
   bool     be;
   bool     swing;
  };
SOrd g_orders[];

bool HasOrder(const long uid)
  {
   for(int i = 0; i < ArraySize(g_orders); i++)
      if(g_orders[i].uid == uid && (g_orders[i].stat == -9 || g_orders[i].stat == 0))
         return true;
   return false;
  }

int LiveCount()
  {
   int n = 0;
   for(int i = 0; i < ArraySize(g_orders); i++)
      if(g_orders[i].stat == -9 || g_orders[i].stat == 0)
         n++;
   return n;
  }

// Master class image 44 names exactly which zones may BE a target:
// "V.S, V.R, PO2 Fresh, PO2 Inversion, False breakout area". A plain untested
// S/R, an SBR/RBS or an SRR/RSS is a place to enter FROM, not to aim AT.
bool CanBeTarget(const int i)
  {
   return (g_zones[i].state == 1 || g_zones[i].state == 2 || g_zones[i].fba);
  }

// the Nth opposite-role zone level ahead of the entry (rank 0 = nearest)
double NextZone(const bool isBuy, const double entry, const int rank)
  {
   double prev = 0.0;
   bool   have = false;
   double cur  = 0.0;
   for(int r = 0; r <= rank; r++)
     {
      bool found = false;
      cur = 0.0;
      for(int i = 0; i < ArraySize(g_zones); i++)
        {
         if(g_zones[i].dead || !CanBeTarget(i))
            continue;
         double lvl = isBuy ? g_zones[i].bot : g_zones[i].top;
         bool ahead = isBuy ? (g_zones[i].role == -1 && lvl > entry)
                      : (g_zones[i].role == 1 && lvl < entry);
         if(!ahead)
            continue;
         if(have && (isBuy ? lvl <= prev : lvl >= prev))
            continue;
         if(!found || (isBuy ? lvl < cur : lvl > cur))
           {
            cur = lvl;
            found = true;
           }
        }
      if(!found)
         return 0.0;
      prev = cur;
      have = true;
     }
   return cur;
  }

void PushOrder(const bool buy, const bool po2, const long uid, const int bar,
               const datetime t, const double entry, const double sl,
               const double tp1, const double tp2, const double tp3,
               const string zone, const double risk, const bool swing)
  {
   int n = ArraySize(g_orders);
   ArrayResize(g_orders, n + 1);
   g_orders[n].buy = buy;    g_orders[n].po2 = po2;   g_orders[n].uid = uid;
   g_orders[n].bar = bar;    g_orders[n].time = t;    g_orders[n].entry = entry;
   g_orders[n].sl = sl;      g_orders[n].tp1 = tp1;   g_orders[n].tp2 = tp2;
   g_orders[n].tp3 = tp3;    g_orders[n].zone = zone; g_orders[n].stat = -9;
   g_orders[n].be = false;   g_orders[n].swing = swing; g_orders[n].risk0 = risk;
   if(ArraySize(g_orders) > 60)
      ArrayRemove(g_orders, 0, 1);
  }

//+------------------------------------------------------------------+
//| The book marks zones only on Weekly / Daily / 4H / 1H, and        |
//| "Timeframe = Pips". So the analysis timeframe is ONE rung up that  |
//| ladder {15m, 1H, 4H, D, W}:                                       |
//|   1m/5m -> 15m · 15m/30m -> 1H · 1H -> 4H · 4H -> D · D -> W      |
//+------------------------------------------------------------------+
ENUM_TIMEFRAMES AnalysisTF()
  {
   if(InpZoneTF != PERIOD_CURRENT)
      return InpZoneTF;
   // The captain's ladder, two rungs up with the middle one skipped:
   //   1m -> 15m,  5m -> 30m,  15m -> 1H,  30m -> 4H,  1H -> D,  4H -> W
   int m = PeriodSeconds(_Period) / 60;
   if(m <= 1)    return PERIOD_M15;
   if(m <= 5)    return PERIOD_M30;
   if(m <= 15)   return PERIOD_H1;
   if(m <= 30)   return PERIOD_H4;
   if(m <= 60)   return PERIOD_D1;
   if(m <= 240)  return PERIOD_W1;
   return PERIOD_MN1;
  }
//+------------------------------------------------------------------+
int OnInit()
  {
   SetIndexBuffer(0, BufBuy,     INDICATOR_DATA);
   SetIndexBuffer(1, BufSell,    INDICATOR_DATA);
   SetIndexBuffer(2, BufPO2Buy,  INDICATOR_DATA);
   SetIndexBuffer(3, BufPO2Sell, INDICATOR_DATA);

   PlotIndexSetInteger(0, PLOT_ARROW, 233);  // up arrow
   PlotIndexSetInteger(1, PLOT_ARROW, 234);  // down arrow
   PlotIndexSetInteger(2, PLOT_ARROW, 225);  // thick up
   PlotIndexSetInteger(3, PLOT_ARROW, 226);  // thick down

   PlotIndexSetDouble(0, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(1, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(2, PLOT_EMPTY_VALUE, EMPTY_VALUE);
   PlotIndexSetDouble(3, PLOT_EMPTY_VALUE, EMPTY_VALUE);

   g_atf = AnalysisTF();

   g_atrHandle    = iATR(_Symbol, _Period, 14);
   g_atrHtfHandle = iATR(_Symbol, g_atf,   14);
   if(g_atrHandle == INVALID_HANDLE || g_atrHtfHandle == INVALID_HANDLE)
      return INIT_FAILED;

   IndicatorSetString(INDICATOR_SHORTNAME,
                      "SNRZ [Zindan] " + EnumToString(g_atf) + " zones");
   return INIT_SUCCEEDED;
  }
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   ObjectsDeleteAll(0, g_prefix);
   if(g_atrHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHandle);
   if(g_atrHtfHandle != INVALID_HANDLE)
      IndicatorRelease(g_atrHtfHandle);
  }
//+------------------------------------------------------------------+
//| Rectangle helpers                                                 |
//+------------------------------------------------------------------+
string ZoneName(const long id)     { return g_prefix + "Z_" + (string)id; }
string ZoneLblName(const long id)  { return g_prefix + "L_" + (string)id; }
// the wide analysis band a refined zone was carved out of - outline only
string ZoneRawName(const long id)  { return g_prefix + "W_" + (string)id; }

color ZoneColor(const SZone &z)
  {
   if(z.state == 2) return InpColInv;
   return z.role == 1 ? InpColSup : InpColRes;
  }

string ZoneText(const SZone &z)
  {
   string base;
   if(z.role == 1)
      base = (z.state == 2 ? (z.wasValid ? "I.VR" : "RBS") : (z.srr ? "SRR" : (z.state == 1 ? "V.S" : "S")));
   else
      base = (z.state == 2 ? (z.wasValid ? "I.VS" : "SBR") : (z.srr ? "RSS" : (z.state == 1 ? "V.R" : "R")));
   if(z.fba)
      base += " FBA";
   if(z.dead)
      base += " x";
   else if(z.touches > 0)
      base += " T" + IntegerToString(z.touches);
   return base;
  }

void DeleteZone(const SZone &z);
void RefineFromChart(double &top, double &bot, const int role,
                     const datetime born, const double atr);

// Images 39 and 44: a zone that is still plain FRESH gives no entry and cannot
// be a target either, so by default it stays off the chart. What the method
// trades is a zone that has JUST become valid, inverted, or an FBA.
bool ZoneVisible(const SZone &z)
  {
   if(z.dead && InpDropDead)
      return false;
   if(z.far)
      return false;
   if(InpShowFresh)
      return true;
   return (z.state == 1 || z.state == 2 || z.fba || z.srr);
  }

void DrawZone(const SZone &z, const datetime t1, const datetime t2)
  {
   string nm = ZoneName(z.id);
   if(!ZoneVisible(z))
     {
      DeleteZone(z);
      return;
     }
   if(ObjectFind(0, nm) < 0)
     {
      ObjectCreate(0, nm, OBJ_RECTANGLE, 0, t1, z.top, t2, z.bot);
      ObjectSetInteger(0, nm, OBJPROP_FILL, true);
      ObjectSetInteger(0, nm, OBJPROP_BACK, true);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, nm, OBJPROP_TIME, 0, t1);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 0, z.top);
   ObjectSetInteger(0, nm, OBJPROP_TIME, 1, t2);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 1, z.bot);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, ZoneColor(z));

   // where the ANALYSIS timeframe saw the level, before the chart candles
   // redrew it. Outline only, so the tight band stays the one you read.
   string rw = ZoneRawName(z.id);
   if(z.refined)
     {
      if(ObjectFind(0, rw) < 0)
        {
         ObjectCreate(0, rw, OBJ_RECTANGLE, 0, t1, z.rawTop, t2, z.rawBot);
         ObjectSetInteger(0, rw, OBJPROP_FILL, false);
         ObjectSetInteger(0, rw, OBJPROP_BACK, true);
         ObjectSetInteger(0, rw, OBJPROP_STYLE, STYLE_DOT);
         ObjectSetInteger(0, rw, OBJPROP_SELECTABLE, false);
         ObjectSetInteger(0, rw, OBJPROP_HIDDEN, true);
        }
      ObjectSetInteger(0, rw, OBJPROP_TIME,  0, t1);
      ObjectSetDouble (0, rw, OBJPROP_PRICE, 0, z.rawTop);
      ObjectSetInteger(0, rw, OBJPROP_TIME,  1, t2);
      ObjectSetDouble (0, rw, OBJPROP_PRICE, 1, z.rawBot);
      ObjectSetInteger(0, rw, OBJPROP_COLOR, ZoneColor(z));
     }

   string ln = ZoneLblName(z.id);
   if(ObjectFind(0, ln) < 0)
     {
      ObjectCreate(0, ln, OBJ_TEXT, 0, t2, (z.top + z.bot) / 2.0);
      ObjectSetInteger(0, ln, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, ln, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, ln, OBJPROP_FONTSIZE, 8);
      ObjectSetInteger(0, ln, OBJPROP_ANCHOR, ANCHOR_LEFT);
     }
   ObjectSetInteger(0, ln, OBJPROP_TIME, 0, t2);
   ObjectSetDouble (0, ln, OBJPROP_PRICE, 0, (z.top + z.bot) / 2.0);
   // a zone that has only just formed is the one the method actually trades
   string txt = ZoneText(z);
   if(InpNewBars > 0 && !z.dead &&
      (long)(t2 - z.bornTime) <= (long)InpNewBars * PeriodSeconds(_Period))
      txt = "NEW " + txt;
   ObjectSetString (0, ln, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, ln, OBJPROP_COLOR, ZoneColor(z));
  }

void DeleteZone(const SZone &z)
  {
   ObjectDelete(0, ZoneName(z.id));
   ObjectDelete(0, ZoneLblName(z.id));
   ObjectDelete(0, ZoneRawName(z.id));
  }

void RemoveZoneAt(const int idx)
  {
   int n = ArraySize(g_zones);
   if(idx < 0 || idx >= n)
      return;
   DeleteZone(g_zones[idx]);
   for(int i = idx; i < n - 1; i++)
      g_zones[i] = g_zones[i + 1];
   ArrayResize(g_zones, n - 1);
  }
//+------------------------------------------------------------------+
//| Zone overlap check                                                |
//+------------------------------------------------------------------+
// an exhausted (dead) zone must not keep the area reserved forever — once a
// zone has had its touches the book says you redraw it
bool Overlaps(const double top, const double bot, const bool htf)
  {
   // within a set only: a small chart zone is expected to sit inside a big
   // analysis zone, and an exhausted zone reserves nothing
   for(int i = 0; i < ArraySize(g_zones); i++)
      if(g_zones[i].htf == htf && !g_zones[i].dead &&
         !(bot > g_zones[i].top || top < g_zones[i].bot))
         return true;
   return false;
  }
//+------------------------------------------------------------------+
//| Add zone (with SNRZ min/max height clamps)                        |
//+------------------------------------------------------------------+
//+------------------------------------------------------------------+
//| Swing memory — book p24: four of the five ways to draw a zone     |
//| pair TWO swing points at a similar price (S+S, R+R, S+R, R+S).    |
//| The band the two of them bracket IS the zone. One lone pivot is   |
//| not a zone, and marking one at every pivot is what filled charts  |
//| with levels the market had never respected twice.                 |
//+------------------------------------------------------------------+
struct SSwing
  {
   double price;
   bool   isHigh;
   int    bar;        // chart bar (or analysis bar for the HTF set)
   bool   line;       // a LINE-CHART swing: a pivot of CLOSES, no wicks
   double stLo;       // the turn this pivot sits in: wick side out to the
   double stHi;       // extreme, body side to the closes
  };
SSwing g_swLtf[], g_swHtf[];

void PushSwing(const bool htf, const double price, const bool isHigh,
               const int bar, const bool line,
               const double stLo, const double stHi)
  {
   int n;
   if(htf)
     {
      n = ArraySize(g_swHtf);
      ArrayResize(g_swHtf, n + 1);
      g_swHtf[n].price = price; g_swHtf[n].isHigh = isHigh;
      g_swHtf[n].bar = bar;     g_swHtf[n].line = line;
      g_swHtf[n].stLo = stLo;   g_swHtf[n].stHi = stHi;
      if(ArraySize(g_swHtf) > 60) ArrayRemove(g_swHtf, 0, 1);
     }
   else
     {
      n = ArraySize(g_swLtf);
      ArrayResize(g_swLtf, n + 1);
      g_swLtf[n].price = price; g_swLtf[n].isHigh = isHigh;
      g_swLtf[n].bar = bar;     g_swLtf[n].line = line;
      g_swLtf[n].stLo = stLo;   g_swLtf[n].stHi = stHi;
      if(ArraySize(g_swLtf) > 60) ArrayRemove(g_swLtf, 0, 1);
     }
  }

//+------------------------------------------------------------------+
//| Image 35: a valid zone has a FIRST movement as well as a second,  |
//| "and the more each of the movements is momentum, the more the     |
//| market respects that zone". These two helpers measure the first   |
//| one: the extreme price reached between the mate swing and this    |
//| one. They read the same bar-index space the swings are stored in. |
//+------------------------------------------------------------------+
int FindMate(const bool htf, const double price, const int bar, const double atr, const bool line);

//+------------------------------------------------------------------+
//| How many separate LEVELS a set of broken zones represents. Zones  |
//| drawn by the candle pass, the line pass and the analysis pass sit |
//| on top of each other, so three zones giving way together is       |
//| usually one level, not three.                                     |
//+------------------------------------------------------------------+
int DistinctLevels(double &px[], const double atr)
  {
   int n = ArraySize(px);
   if(n <= 0)
      return 0;
   ArraySort(px);
   double tol = atr * InpPairTolATR;
   int    out = 1;
   double last = px[0];
   for(int i = 1; i < n; i++)
      if(px[i] - last > tol)
        {
         out++;
         last = px[i];
        }
   return out;
  }

void MateSeg(const bool htf, const double price, const int bar, const double atr,
             const double &hi[], const double &lo[], double &outHi, double &outLo,
             const bool line = false)
  {
   outHi = -DBL_MAX; outLo = DBL_MAX;
   int mi = FindMate(htf, price, bar, atr, line);
   if(mi < 0) return;
   int mb = htf ? g_swHtf[mi].bar : g_swLtf[mi].bar;
   if(mb < 0 || mb > bar) return;
   outHi = hi[mb]; outLo = lo[mb];
   for(int k = mb; k <= bar; k++)
     {
      outHi = MathMax(outHi, hi[k]);
      outLo = MathMin(outLo, lo[k]);
     }
  }

void MateSegRates(const double price, const int bar, const double atr,
                  const MqlRates &r[], double &outHi, double &outLo,
                  const bool line = false,
                  const double stLo = 0.0, const double stHi = 0.0)
  {
   outHi = -DBL_MAX; outLo = DBL_MAX;
   int mi = FindMate(true, price, bar, atr, line);
   if(mi < 0) return;
   int mb = g_swHtf[mi].bar;
   if(mb < 0 || mb > bar) return;
   // a LINE swing has no wicks, so its movement is measured close to close.
   // Reading high/low here measured the first movement on a chart the zone was
   // never drawn from - the same mistake the engulf path once had.
   outHi = line ? r[mb].close : r[mb].high;
   outLo = line ? r[mb].close : r[mb].low;
   for(int k = mb; k <= bar; k++)
     {
      outHi = MathMax(outHi, line ? r[k].close : r[k].high);
      outLo = MathMin(outLo, line ? r[k].close : r[k].low);
     }
  }

//+------------------------------------------------------------------+
//| Images 27/28 — the ENGULF zone, method 5 of p24: "mark it where   |
//| the wicks have befriended, and whichever candle is SHORTER than   |
//| the one before it". Needs a real engulfing pair around the pivot  |
//| (bearish at a top, bullish at a bottom); the band is the shorter  |
//| candle's body. Leaves top<=bot when the pivot has no such pair.   |
//+------------------------------------------------------------------+
void EngulfPair(const double &o[], const double &h[], const double &l[],
                const double &c[], const int p, const bool isHigh,
                const int n, double &outTop, double &outBot)
  {
   outTop = 0.0; outBot = 1.0;                 // "nothing found"
   for(int k = 0; k <= 1; k++)
     {
      int i1 = p - 1 + k;                      // the candle that gets engulfed
      int i2 = p + k;                          // the one that engulfs it
      if(i1 < 0 || i2 >= n)
         continue;
      bool up = c[i2] > o[i2];
      if(isHigh == up)                         // top needs bearish, bottom bullish
         continue;
      double b1h = MathMax(o[i1], c[i1]), b1l = MathMin(o[i1], c[i1]);
      double b2h = MathMax(o[i2], c[i2]), b2l = MathMin(o[i2], c[i2]);
      if(!(b2h >= b1h && b2l <= b1l && b2h > b2l))
         continue;
      int si = (h[i1] - l[i1]) <= (h[i2] - l[i2]) ? i1 : i2;
      // the zone is that WHOLE candle - body together with its wick
      outTop = InpEngulfFullCandle ? h[si] : MathMax(o[si], c[si]);
      outBot = InpEngulfFullCandle ? l[si] : MathMin(o[si], c[si]);
      return;
     }
  }

void EngulfPairRates(const MqlRates &r[], const int p, const bool isHigh,
                     const int n, double &outTop, double &outBot)
  {
   outTop = 0.0; outBot = 1.0;
   for(int k = 0; k <= 1; k++)
     {
      int i1 = p - 1 + k, i2 = p + k;
      if(i1 < 0 || i2 >= n)
         continue;
      bool up = r[i2].close > r[i2].open;
      if(isHigh == up)
         continue;
      double b1h = MathMax(r[i1].open, r[i1].close), b1l = MathMin(r[i1].open, r[i1].close);
      double b2h = MathMax(r[i2].open, r[i2].close), b2l = MathMin(r[i2].open, r[i2].close);
      if(!(b2h >= b1h && b2l <= b1l && b2h > b2l))
         continue;
      int si = (r[i1].high - r[i1].low) <= (r[i2].high - r[i2].low) ? i1 : i2;
      outTop = InpEngulfFullCandle ? r[si].high : MathMax(r[si].open, r[si].close);
      outBot = InpEngulfFullCandle ? r[si].low  : MathMin(r[si].open, r[si].close);
      return;
     }
  }

// index of the newest earlier swing at a similar price, or -1
int FindMate(const bool htf, const double price, const int bar, const double atr, const bool line)
  {
   double tol = atr * InpPairTolATR;
   int n = htf ? ArraySize(g_swHtf) : ArraySize(g_swLtf);
   int first = MathMax(0, n - InpPairLookback);
   for(int i = n - 1; i >= first; i--)
     {
      int    sb = htf ? g_swHtf[i].bar   : g_swLtf[i].bar;
      double sp = htf ? g_swHtf[i].price : g_swLtf[i].price;
      bool   sl = htf ? g_swHtf[i].line  : g_swLtf[i].line;
      // a CLOSE pivot may only pair with another close pivot - mixing the two
      // would glue a line chart onto a candle chart
      if(sl == line && bar - sb <= InpPairMaxGap && MathAbs(sp - price) <= tol)
         return i;
     }
   return -1;
  }

void AddZone(double top, double bot, const int role, const int bornH, const double atr,

             const datetime t1, const datetime t2, const bool htf, const bool paired)
  {
   double h  = top - bot;
   double mn = atr * InpMinZoneATR;
   // the analysis zone takes its shape from the structure of the turn, so its
   // cap is a sanity bound rather than a shape
   double mx = atr * (htf ? InpMaxZoneATRHtf : InpMaxZoneATR);
   if(h < mn)
     {
      double mid = (top + bot) / 2.0;
      top = mid + mn / 2.0;
      bot = mid - mn / 2.0;
     }
   if(top - bot > mx)
     {
      if(role == 1) top = bot + mx;
      else          bot = top - mx;
     }
   double rawTop = top, rawBot = bot;
   bool   refined = false;
   if(htf && InpRefineHtf)
     {
      RefineFromChart(top, bot, role, t2, atr);
      refined = (top != rawTop || bot != rawBot);
     }
   int n = ArraySize(g_zones);
   ArrayResize(g_zones, n + 1);
   g_zones[n].top        = top;
   g_zones[n].bot        = bot;
   g_zones[n].role       = role;
   // p24/p35: a paired zone was drawn FROM its two movements, so it is VALID
   g_zones[n].state      = paired ? 1 : 0;
   g_zones[n].touches    = 0;
   g_zones[n].oppBreaks  = 0;
   g_zones[n].srr        = false;
   g_zones[n].wasValid   = false;
   g_zones[n].dead       = false;
   g_zones[n].flips      = 0;
   g_zones[n].htf        = htf;
   g_zones[n].sigTouch   = 0;
   g_zones[n].bornH      = bornH;
   g_zones[n].bornTime   = t1;
   g_zones[n].activeFrom = t2;
   g_zones[n].id         = ++g_zoneSeq;
   g_zones[n].inZonePrev = false;
   g_zones[n].paired     = paired;
   g_zones[n].far        = false;
   g_zones[n].awaitPull  = -1;
   g_zones[n].rawTop = rawTop;
   g_zones[n].rawBot = rawBot;
   g_zones[n].refined = refined;
   g_zones[n].falseBreaks = 0;
   g_zones[n].fba        = false;
   g_zones[n].pendBar    = -1;
   g_zones[n].pendDir    = 0;
   DrawZone(g_zones[n], t1, t2);

   int cap = htf ? InpMaxZonesHtf : InpMaxZonesLtf;
   while(true)
     {
      int cnt = 0;
      for(int i = 0; i < ArraySize(g_zones); i++)
         if(g_zones[i].htf == htf)
            cnt++;
      if(cnt <= cap)
         break;
      int victim = -1;                     // evict an exhausted zone first
      for(int i = 0; i < ArraySize(g_zones); i++)
         if(g_zones[i].htf == htf && g_zones[i].dead)
           {
            victim = i;
            break;
           }
      if(victim < 0)
         for(int i = 0; i < ArraySize(g_zones); i++)
            if(g_zones[i].htf == htf)
              {
               victim = i;
               break;
              }
      if(victim < 0)
         break;
      RemoveZoneAt(victim);
     }
  }
//+------------------------------------------------------------------+
//| A broken level only becomes tradable where the PULLBACK turns.   |
//| The captain's 30m chart: an R at 4,433.9 breaks upward, price    |
//| runs to 4,530, then pulls back and prints an S at ~4,460. That S |
//| - not the old band, which price never revisits - is the buy. So  |
//| a flipped zone waits for the first swing of the matching kind on |
//| the correct side of the break and MOVES onto it, keeping its id  |
//| and wasValid so it is still named I.VR / RBS as before.          |
//+------------------------------------------------------------------+
void ReanchorFlipped(const double price, const bool isHigh, const double atr,
                     const bool htf, const int bar,
                     const double &hi[], const double &lo[])
  {
   if(!InpFlipNeedsPullback)
      return;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].awaitPull < 0 || g_zones[i].dead || g_zones[i].htf != htf)
         continue;
      if(bar <= g_zones[i].awaitPull)
         continue;
      bool wantLow = (g_zones[i].role == 1);
      if(wantLow == isHigh)
         continue;
      // a pullback that cuts back through the old level is a failed break,
      // which the false-breakout path handles instead
      if(wantLow  && price < g_zones[i].bot) continue;
      if(!wantLow && price > g_zones[i].top) continue;
      // ...and the retracement must follow a real move away from the level
      if(InpNeedBigMove)
        {
         double ext = wantLow ? hi[g_zones[i].awaitPull] : lo[g_zones[i].awaitPull];
         for(int k = g_zones[i].awaitPull; k <= bar; k++)
            ext = wantLow ? MathMax(ext, hi[k]) : MathMin(ext, lo[k]);
         double gone = wantLow ? (ext - g_zones[i].top) : (g_zones[i].bot - ext);
         if(gone < atr * InpBigMoveATR)
            continue;
        }
      double half = MathMax(atr * InpMinZoneATR, _Point) / 2.0;
      g_zones[i].top = price + half;
      g_zones[i].bot = price - half;
      g_zones[i].awaitPull = -1;
     }
  }

//+------------------------------------------------------------------+
//| The captain's middle layer, in his own words: when the big zone  |
//| is marked on 15m and you go to 1m, that zone gets SMALLER,       |
//| because the candles are smaller. On 1m the 15-minute zone is     |
//| LIFTED and the zone the 1-minute chart drew is what counts; when |
//| price pulls back to it, the trade is taken.                      |
//|                                                                  |
//| The key is WHICH candles redraw it. Not price returning later -  |
//| the 15m zone and the 1m zone are drawn from THE SAME MINUTES at  |
//| a finer resolution. One 15m candle with a five-dollar wick is    |
//| fifteen 1m candles and the turn only used three of them. So the  |
//| chart candles that built this band are re-read, the one holding  |
//| the extreme is found, and the band is redrawn around ITS little  |
//| cluster with the ordinary SNRZ geometry: wick side out to the    |
//| extreme, body side in to the closes.                             |
//|                                                                  |
//| rawTop/rawBot keep the wide band for the drawing; the order, the |
//| stop and the touch count all belong to the tight one.            |
//+------------------------------------------------------------------+
void RefineFromChart(double &top, double &bot, const int role,
                     const datetime born, const double atr)
  {
   // the analysis pivot needs InpPivotHtf bars each side to confirm, so its
   // turn lies within that many analysis bars of chart candles
   double ratio = (double)PeriodSeconds(g_atf) / (double)PeriodSeconds(_Period);
   int span = (int)MathMin(400.0, MathMax(1.0, ratio) * (2 * InpPivotHtf + 1));
   int base = iBarShift(_Symbol, _Period, born, false);
   if(base < 0)
      return;

   int    best = -1;
   double bestPx = 0.0;
   for(int k = 0; k <= span; k++)
     {
      int    sh = base + k;
      double h  = iHigh(_Symbol, _Period, sh);
      double l  = iLow (_Symbol, _Period, sh);
      if(h <= 0.0 || l <= 0.0)
         break;                                  // ran out of history
      if(h < bot || l > top)
         continue;                               // never visited the band
      double px = MathMax(bot, MathMin(top, role == 1 ? l : h));
      if(best < 0 || (role == 1 ? px < bestPx : px > bestPx))
        {
         best   = sh;
         bestPx = px;
        }
     }
   if(best < 0)
      return;

   int    k0 = InpStructBars;
   double ex = 0.0, body = 0.0;
   bool   got = false;
   for(int sh = MathMax(0, best - k0); sh <= best + k0; sh++)
     {
      double o = iOpen (_Symbol, _Period, sh);
      double c = iClose(_Symbol, _Period, sh);
      double h = iHigh (_Symbol, _Period, sh);
      double l = iLow  (_Symbol, _Period, sh);
      if(o <= 0.0 || c <= 0.0)
         continue;
      double e = (role == 1) ? l : h;
      double y = (role == 1) ? MathMax(o, c) : MathMin(o, c);
      if(!got)
        {
         ex = e;  body = y;  got = true;
        }
      else if(role == 1)
        {
         ex = MathMin(ex, e);  body = MathMax(body, y);
        }
      else
        {
         ex = MathMax(ex, e);  body = MathMin(body, y);
        }
     }
   if(!got)
      return;

   double t, b;
   if(role == 1)
     {
      b = MathMax(bot, ex);
      t = MathMin(top, body);
     }
   else
     {
      t = MathMin(top, ex);
      b = MathMax(bot, body);
     }
   double mn = atr * InpMinZoneATR;
   if(t - b < mn)                                // still a zone, not a hair
     {
      double m = (t + b) / 2.0;
      t = MathMin(top, m + mn / 2.0);
      b = MathMax(bot, m - mn / 2.0);
     }
   if(t <= b || t - b >= top - bot)
      return;                                    // no tighter than it was
   top = t;
   bot = b;
  }

//+------------------------------------------------------------------+
//| The turn a pivot sits in: wick side out to the extreme, body side |
//| to the closes. Stored with the swing so a later pair can widen    |
//| its zone to cover BOTH turns - the shape of the captain's boxes.  |
//+------------------------------------------------------------------+
void StructBand(const double &o[], const double &h[], const double &l[],
                const double &c[], const int p, const int n, const bool isHigh,
                double &outLo, double &outHi)
  {
   int k = InpStructBars;
   int a = MathMax(0, p - k), z = MathMin(n - 1, p + k);
   outLo = l[a]; outHi = h[a];
   double bodyHi = MathMax(o[a], c[a]), bodyLo = MathMin(o[a], c[a]);
   for(int i = a; i <= z; i++)
     {
      outLo  = MathMin(outLo, l[i]);
      outHi  = MathMax(outHi, h[i]);
      bodyHi = MathMax(bodyHi, MathMax(o[i], c[i]));
      bodyLo = MathMin(bodyLo, MathMin(o[i], c[i]));
     }
   // a low pivot keeps its wick low and stops at the bodies above it
   if(!isHigh) outHi = bodyHi;
   else        outLo = bodyLo;
  }

void StructBandRates(const MqlRates &r[], const int p, const int n,
                     const bool isHigh, double &outLo, double &outHi)
  {
   int k = InpStructBars;
   int a = MathMax(0, p - k), z = MathMin(n - 1, p + k);
   outLo = r[a].low; outHi = r[a].high;
   double bodyHi = MathMax(r[a].open, r[a].close);
   double bodyLo = MathMin(r[a].open, r[a].close);
   for(int i = a; i <= z; i++)
     {
      outLo  = MathMin(outLo, r[i].low);
      outHi  = MathMax(outHi, r[i].high);
      bodyHi = MathMax(bodyHi, MathMax(r[i].open, r[i].close));
      bodyLo = MathMin(bodyLo, MathMin(r[i].open, r[i].close));
     }
   if(!isHigh) outHi = bodyHi;
   else        outLo = bodyLo;
  }

//+------------------------------------------------------------------+
//| One confirmed swing -> at most one zone, drawn with its mate      |
//+------------------------------------------------------------------+
void AddSwingZone(const bool htf, const double price, const bool isHigh,
                  const int bar, const int bornH, const double atr,
                  const double bodyHi, const double bodyLo,
                  const double hiRun, const double loRun,
                  const double refClose, const datetime t1, const datetime t2,
                  const double firstHi, const double firstLo,
                  const double engTop, const double engBot, const double momBody,
                  const double momHi, const double momLo,
                  const bool line = false,
                  // The structural band of THIS pivot's turn. These two were
                  // used in the body (PushSwing, and the InpStructZone widen
                  // below) and passed by four call sites, but never declared —
                  // so the indicator had not compiled since v10.7.
                  const double stLo = 0.0, const double stHi = 0.0)
  {
   int mi = InpPairZones ? FindMate(htf, price, bar, atr, line) : -1;
   double matePrice = 0.0;
   bool   mateHigh  = false;
   double mateStLo  = 0.0, mateStHi = 0.0;
   if(mi >= 0)
     {
      matePrice = htf ? g_swHtf[mi].price  : g_swLtf[mi].price;
      mateHigh  = htf ? g_swHtf[mi].isHigh : g_swLtf[mi].isHigh;
      mateStLo  = htf ? g_swHtf[mi].stLo   : g_swLtf[mi].stLo;
      mateStHi  = htf ? g_swHtf[mi].stHi   : g_swLtf[mi].stHi;
     }
   PushSwing(htf, price, isHigh, bar, line, stLo, stHi);

   // A GAP mate is the OPPOSITE kind of swing at a DIFFERENT price, so the
   // same-price search above can never find one - it needs its own pass.
   int gi = -1;
   if(InpGapZones && (htf || !InpGapHtfOnly))
     {
      double glo = atr * InpGapMinATR, ghi = atr * InpGapMaxATR;
      int gn = htf ? ArraySize(g_swHtf) : ArraySize(g_swLtf);
      int gfirst = MathMax(0, gn - InpPairLookback - 1);
      for(int k = gn - 2; k >= gfirst; k--)
        {
         double kp = htf ? g_swHtf[k].price  : g_swLtf[k].price;
         bool   kh = htf ? g_swHtf[k].isHigh : g_swLtf[k].isHigh;
         int    kb = htf ? g_swHtf[k].bar    : g_swLtf[k].bar;
         double kd = MathAbs(kp - price);
         bool kl = htf ? g_swHtf[k].line : g_swLtf[k].line;
         if(kl == line && bar - kb <= InpPairMaxGap && kh != isHigh && kd >= glo && kd <= ghi)
           {
            gi = k;
            break;
           }
        }
     }
   if(gi >= 0)
     {
      double gp = htf ? g_swHtf[gi].price : g_swLtf[gi].price;
      double gt = MathMax(gp, price), gb = MathMin(gp, price);
      // images 31/33: which side the gap is traded from is decided by where
      // price IS - above the band it is support underneath, below it is
      // resistance overhead. While price stands INSIDE the band there is no
      // side yet, and images 52/53 both draw the box with price already
      // outside it and wait for the return. The old midpoint tie-break gave
      // such a band a side by accident.
      int grole = (refClose > gt) ? 1 : (refClose < gb ? -1 : 0);
      if(grole != 0 && !Overlaps(gt, gb, htf))
         AddZone(gt, gb, grole, bornH, atr, t1, t2, htf, true);
      return;
     }

   if(mi < 0 && line)
     {
      // The captain's own 15m line chart, tagged by hand: EVERY swing that
      // makes a peak carries an R and every swing that makes a trough an S.
      // No size filter, no higher-high rule. These are plain FRESH levels -
      // not entries (image 39) and not targets (image 44) until they earn a
      // second touch - but SBR/RBS are exactly what a PLAIN level becomes
      // when it breaks, so the line chart needs them.
      if(InpLineSingleLevels && !Overlaps(price, price, htf))
         AddZone(price, price, isHigh ? -1 : 1, bornH, atr, t1, t2, htf, false);
      return;
     }

   if(mi < 0)
     {
      // p24 method 5, and the only two things the book draws on a SINGLE
      // swing: the ENGULF pair (images 27/28 — the shorter candle's body) and
      // the MOMENTUM candle (image 43). Until v9.7 this drew a zone at every
      // unpaired pivot, which is a rule the book does not have; 70% of all
      // zones came from here. An unpaired zone is born FRESH either way.
      double bigM = atr * InpBigMoveATR;
      double ft = 0.0, fb = 0.0;
      bool   got = false;
      if(InpEngulfZones && engTop > engBot)
        {
         ft = engTop; fb = engBot; got = true;
        }
      else
         if(InpMomentumZones && momBody >= atr * InpMomBodyATR)
           {
            // Image 43 boxes the WHOLE candle - body together with both wicks,
            // exactly as the engulf rule does. Taking only the wick side left
            // the zone a sliver on top of a candle whose whole point is its
            // body. Confirmed by the captain.
            if(InpMomFullCandle && momHi > momLo)
              {
               ft = momHi;
               fb = momLo;
              }
            else
              {
               ft = isHigh ? price  : bodyLo;
               fb = isHigh ? bodyHi : price;
              }
            got = (ft > fb);
           }
      if(got)
        {
         double away = isHigh ? (ft - loRun) : (hiRun - fb);
         if(away >= bigM && !Overlaps(ft, fb, htf))
            AddZone(ft, fb, isHigh ? -1 : 1, bornH, atr, t1, t2, htf, false);
        }
      return;
     }

   double t = MathMax(matePrice, price);
   double b = MathMin(matePrice, price);
   int role;
   if(isHigh && mateHigh)        role = -1;             // R+R
   else if(!isHigh && !mateHigh) role =  1;             // S+S
   else                                                 // S+R / R+S — the
      role = (refClose > (t + b) / 2.0) ? 1 : -1;       // GAP band (p51)
   // image 36: the move AWAY must be big — and image 35: so must the FIRST
   // movement, the stretch between the two touches.
   double bigA  = atr * InpBigMoveATR;
   double away  = (role == 1) ? (hiRun - t) : (b - loRun);
   double first = (role == 1) ? (firstHi - t) : (b - firstLo);
   // "if ONE of them, the first movement or the second, is bigger, it is
   // BETTER" - one, not both, and better, not required.
   bool bigAway  = (away >= bigA);
   bool bigFirst = (firstHi > -DBL_MAX && first >= bigA);
   bool okBig = (InpBigMoveRule == BIGMOVE_OFF) ? true
                : (InpBigMoveRule == BIGMOVE_BOTH ? (bigAway && bigFirst)
                                                  : (bigAway || bigFirst));
   // The analysis zone takes the shape of BOTH turns, not a hairline through
   // two swing prices: wick side out to the extreme, body side to the closes.
   if(InpStructZone && htf && !line && mateStHi > mateStLo && stHi > stLo)
     {
      b = MathMin(b, MathMin(stLo, mateStLo));
      t = MathMax(t, MathMax(stHi, mateStHi));
     }
   if(!Overlaps(t, b, htf) && okBig)
      AddZone(t, b, role, bornH, atr, t1, t2, htf, true);
  }
//+------------------------------------------------------------------+
//| p14: the small zone sits INSIDE the big one                       |
//+------------------------------------------------------------------+
bool ZoneNested(const int idx)
  {
   if(g_zones[idx].htf)
      return true;
   for(int i = 0; i < ArraySize(g_zones); i++)
      if(g_zones[i].htf && !g_zones[i].dead && g_zones[i].role == g_zones[idx].role &&
         g_zones[idx].bot <= g_zones[i].top && g_zones[idx].top >= g_zones[i].bot)
         return true;
   return false;
  }
//+------------------------------------------------------------------+
//| 75% breakout rule                                                 |
//+------------------------------------------------------------------+
bool BullBreak75(const double lvl, const double o, const double h, const double l, const double c)
  {
   if(c > lvl && o > lvl)              // full body above
      return true;
   if(c <= lvl)
      return false;
   double bodyLow = MathMin(o, c);
   double len = h - bodyLow;           // ignore lower shadow
   if(len <= 0.0)
      return false;
   double outside = h - MathMax(lvl, bodyLow);
   return (outside / len * 100.0) >= InpBreakoutPct;
  }

bool BearBreak75(const double lvl, const double o, const double h, const double l, const double c)
  {
   if(c < lvl && o < lvl)
      return true;
   if(c >= lvl)
      return false;
   double bodyHigh = MathMax(o, c);
   double len = bodyHigh - l;          // ignore upper shadow
   if(len <= 0.0)
      return false;
   double outside = MathMin(lvl, bodyHigh) - l;
   return (outside / len * 100.0) >= InpBreakoutPct;
  }
//+------------------------------------------------------------------+
//| Alert helper                                                      |
//+------------------------------------------------------------------+
void Notify(const string msg, const bool live)
  {
   if(!live)                       // never alert while loading history
      return;
   string full = "SNRZ " + _Symbol + " " + EnumToString((ENUM_TIMEFRAMES)_Period) + ": " + msg;
   if(InpAlertPopup) Alert(full);
   if(InpAlertPush)  SendNotification(full);
  }
//+------------------------------------------------------------------+
//| Position drawing (last setup stays visible, faded when closed)    |
//+------------------------------------------------------------------+
void PosLine(const string tag, const datetime t1, const datetime t2,
             const double price, const color col, const int width, const int style)
  {
   string nm = g_prefix + "P_" + tag;
   if(ObjectFind(0, nm) < 0)
     {
      ObjectCreate(0, nm, OBJ_TREND, 0, t1, price, t2, price);
      ObjectSetInteger(0, nm, OBJPROP_RAY_RIGHT, false);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
     }
   ObjectSetInteger(0, nm, OBJPROP_TIME,  0, t1);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 0, price);
   ObjectSetInteger(0, nm, OBJPROP_TIME,  1, t2);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 1, price);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
   ObjectSetInteger(0, nm, OBJPROP_WIDTH, width);
   ObjectSetInteger(0, nm, OBJPROP_STYLE, style);
  }

void PosText(const string tag, const datetime t, const double price,
             const string txt, const color col)
  {
   string nm = g_prefix + "T_" + tag;
   if(ObjectFind(0, nm) < 0)
     {
      ObjectCreate(0, nm, OBJ_TEXT, 0, t, price);
      ObjectSetInteger(0, nm, OBJPROP_SELECTABLE, false);
      ObjectSetInteger(0, nm, OBJPROP_HIDDEN, true);
      ObjectSetInteger(0, nm, OBJPROP_FONTSIZE, 9);
      ObjectSetInteger(0, nm, OBJPROP_ANCHOR, ANCHOR_LEFT);
     }
   ObjectSetInteger(0, nm, OBJPROP_TIME,  0, t);
   ObjectSetDouble (0, nm, OBJPROP_PRICE, 0, price);
   ObjectSetString (0, nm, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, nm, OBJPROP_COLOR, col);
  }

void ClearPositionObjects()
  {
   for(int i = ObjectsTotal(0) - 1; i >= 0; i--)
     {
      string nm = ObjectName(0, i);
      if(StringFind(nm, g_prefix + "P_") == 0 || StringFind(nm, g_prefix + "T_") == 0)
         ObjectDelete(0, nm);
     }
  }

// draws EVERY live order and trade, not just the newest one
void DrawPosition(const datetime tNow)
  {
   ClearPositionObjects();
   if(!InpShowPosition)
      return;
   datetime t2 = tNow + (datetime)(PeriodSeconds(_Period) * 25);
   for(int i = 0; i < ArraySize(g_orders); i++)
     {
      if(g_orders[i].stat != -9 && g_orders[i].stat != 0)
         continue;
      string  id     = "O" + IntegerToString(i);
      bool    rest   = (g_orders[i].stat == -9);
      color   dirCol = g_orders[i].buy ? clrMediumSeaGreen : clrTomato;
      color   head   = g_orders[i].po2 ? InpColInv : dirCol;
      int     st     = rest ? STYLE_DASH : STYLE_SOLID;
      string  kind   = g_orders[i].swing ? "SWING" : "SCALP";
      string  dir    = g_orders[i].buy ? "BUY" : "SELL";
      string  stat   = rest ? " LIMIT waiting"
                       : (g_orders[i].stat >= 1
                          ? "  v TP" + IntegerToString(g_orders[i].stat) +
                          (g_orders[i].be ? " risk free" : "")
                          : "  . running");
      datetime t1 = g_orders[i].time;
      PosLine(id + "E",  t1, t2, g_orders[i].entry, clrWhite,          2, STYLE_DOT);
      PosLine(id + "SL", t1, t2, g_orders[i].sl,    clrTomato,         2, st);
      PosLine(id + "T1", t1, t2, g_orders[i].tp1,   clrMediumSeaGreen, 2, st);
      PosLine(id + "T2", t1, t2, g_orders[i].tp2,   clrMediumSeaGreen, 1, STYLE_DASH);
      PosLine(id + "T3", t1, t2, g_orders[i].tp3,   clrMediumSeaGreen, 1, STYLE_DASH);
      PosText(id + "E",  t2, g_orders[i].entry, dir + " " + kind +
              (g_orders[i].po2 ? " PO2" : "") + " " + g_orders[i].zone + stat +
              "  " + DoubleToString(g_orders[i].entry, _Digits), head);
      PosText(id + "SL", t2, g_orders[i].sl, (g_orders[i].be ? "SL-BE " : "SL  ") +
              DoubleToString(g_orders[i].sl, _Digits), clrTomato);
      PosText(id + "T1", t2, g_orders[i].tp1, "TP1 " + DoubleToString(g_orders[i].tp1, _Digits), clrMediumSeaGreen);
      PosText(id + "T2", t2, g_orders[i].tp2, "TP2 " + DoubleToString(g_orders[i].tp2, _Digits), clrMediumSeaGreen);
      PosText(id + "T3", t2, g_orders[i].tp3, "TP3 " + DoubleToString(g_orders[i].tp3, _Digits), clrMediumSeaGreen);
     }
  }
//+------------------------------------------------------------------+
//| Process one CLOSED analysis-timeframe bar: pivots, zones, trend   |
//+------------------------------------------------------------------+
void ProcessHtfBar(const int j, const MqlRates &htf[], const double &atrH[], const int hCount)
  {
   double atr = atrH[j];
   if(atr <= 0.0)
      return;

   int p = j - InpPivotHtf;                  // pivot candidate
   if(p >= InpPivotHtf)
     {
      bool isPH = true, isPL = true;
      for(int k = p - InpPivotHtf; k <= p + InpPivotHtf; k++)
        {
         if(k == p) continue;
         if(htf[k].high >= htf[p].high) isPH = false;
         if(htf[k].low  <= htf[p].low)  isPL = false;
         if(!isPH && !isPL) break;
        }
      if(isPH) { g_prevHigh = g_lastHigh; g_lastHigh = htf[p].high; }
      if(isPL) { g_prevLow  = g_lastLow;  g_lastLow  = htf[p].low;  }

      // the movement made AFTER the pivot is the book's "Big Movement"
      double hiRun = htf[p].high, loRun = htf[p].low;
      for(int k = p; k <= j; k++)
        {
         hiRun = MathMax(hiRun, htf[k].high);
         loRun = MathMin(loRun, htf[k].low);
        }
      double bigMove = atr * InpBigMoveATR;

      double bodyHiH = MathMax(htf[p].open, htf[p].close);
      double bodyLoH = MathMin(htf[p].open, htf[p].close);
      double fHi = 0.0, fLo = 0.0, eT = 0.0, eB = 1.0, sLo = 0.0, sHi = 0.0;
      if(isPL)
        {
         MateSegRates(htf[p].low, p, atr, htf, fHi, fLo);
         EngulfPairRates(htf, p, false, hCount, eT, eB);
         StructBandRates(htf, p, hCount, false, sLo, sHi);
         AddSwingZone(true, htf[p].low, false, p, j, atr, bodyHiH, bodyLoH,
                      hiRun, loRun, htf[j].close, htf[p].time, htf[j].time,
                      fHi, fLo, eT, eB, MathAbs(htf[p].close - htf[p].open),
                      htf[p].high, htf[p].low, false, sLo, sHi);
        }
      if(isPH)
        {
         MateSegRates(htf[p].high, p, atr, htf, fHi, fLo);
         EngulfPairRates(htf, p, true, hCount, eT, eB);
         StructBandRates(htf, p, hCount, true, sLo, sHi);
         AddSwingZone(true, htf[p].high, true, p, j, atr, bodyHiH, bodyLoH,
                      hiRun, loRun, htf[j].close, htf[p].time, htf[j].time,
                      fHi, fLo, eT, eB, MathAbs(htf[p].close - htf[p].open),
                      htf[p].high, htf[p].low, false, sLo, sHi);
        }

      //--- and the line chart of the ANALYSIS timeframe
      if(InpLineZones)
        {
         bool lPH = true, lPL = true;
         for(int k = p - InpPivotHtf; k <= p + InpPivotHtf; k++)
           {
            if(k == p) continue;
            if(htf[k].close >= htf[p].close) lPH = false;
            if(htf[k].close <= htf[p].close) lPL = false;
            if(!lPH && !lPL) break;
           }
         if(lPH || lPL)
           {
            double hiRunL = htf[p].close, loRunL = htf[p].close;
            for(int k = p; k <= j; k++)
              {
               hiRunL = MathMax(hiRunL, htf[k].close);
               loRunL = MathMin(loRunL, htf[k].close);
              }
            double lHi = 0.0, lLo = 0.0;
            MateSegRates(htf[p].close, p, atr, htf, lHi, lLo, true);
            AddSwingZone(true, htf[p].close, lPH, p, j, atr, bodyHiH, bodyLoH,
                         hiRunL, loRunL, htf[j].close, htf[p].time, htf[j].time,
                         lHi, lLo, 0.0, 1.0, 0.0, 0.0, 0.0, true);
           }
        }
     }

   // Book: a close beyond the last confirmed swing IS the Break of Structure,
   // and that is what turns the trend. Only the moment it breaks counts.
   double hc = htf[j].close;
   bool bosUp = (g_lastHigh > 0 && hc > g_lastHigh + atr * 0.1);
   bool bosDn = (g_lastLow  > 0 && hc < g_lastLow  - atr * 0.1);
   if(bosUp && !g_bosUpPrev) { g_lastBosUpH = j; g_trendState = 1; }
   if(bosDn && !g_bosDnPrev) { g_lastBosDnH = j; g_trendState = -1; }
   g_bosUpPrev = bosUp;
   g_bosDnPrev = bosDn;
   if(g_trendState == 0 && g_prevHigh > 0 && g_prevLow > 0)
     {
      if(g_lastHigh > g_prevHigh && g_lastLow > g_prevLow)      g_trendState = 1;
      else if(g_lastHigh < g_prevHigh && g_lastLow < g_prevLow) g_trendState = -1;
     }

   // Analysis zones, like chart zones, die by a BREAK — off by default.
   if(InpExpireByAge)
      for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
         if(g_zones[i].htf && j - g_zones[i].bornH > InpLifeHtf)
            RemoveZoneAt(i);
  }
//+------------------------------------------------------------------+
int OnCalculate(const int rates_total,
                const int prev_calculated,
                const datetime &time[],
                const double &open[],
                const double &high[],
                const double &low[],
                const double &close[],
                const long &tick_volume[],
                const long &volume[],
                const int &spread[])
  {
   if(rates_total < MathMax(InpPivotLtf, InpPivotHtf) * 2 + 20)
      return 0;

   //--- analysis-timeframe series -------------------------------------------
   double tfRatio = (double)PeriodSeconds(g_atf) / (double)PeriodSeconds(_Period);
   int need = (int)MathMin(5000.0, rates_total / MathMax(tfRatio, 1.0) + InpPivotHtf * 4 + 60);
   need = MathMax(need, InpPivotHtf * 4 + 60);

   static MqlRates htf[];
   int hCount = CopyRates(_Symbol, g_atf, 0, need, htf);
   if(hCount <= InpPivotHtf * 2 + 5)
      return prev_calculated;               // higher timeframe not loaded yet
   ArraySetAsSeries(htf, false);

   static double atrH[];
   if(CopyBuffer(g_atrHtfHandle, 0, 0, hCount, atrH) <= 0)
      return prev_calculated;
   ArraySetAsSeries(atrH, false);

   static double atrBuf[];
   if(CopyBuffer(g_atrHandle, 0, 0, rates_total, atrBuf) <= 0)
      return prev_calculated;
   ArraySetAsSeries(atrBuf, false);

   static int lastProcessed = -1;
   static int hIdx = -1;

   if(prev_calculated == 0)                 // full recalculation -> clean slate
     {
      ArrayInitialize(BufBuy,     EMPTY_VALUE);
      ArrayInitialize(BufSell,    EMPTY_VALUE);
      ArrayInitialize(BufPO2Buy,  EMPTY_VALUE);
      ArrayInitialize(BufPO2Sell, EMPTY_VALUE);
      ObjectsDeleteAll(0, g_prefix);
      ArrayResize(g_zones, 0);
      lastProcessed = -1;
      hIdx = -1;
      g_lastHigh = g_prevHigh = g_lastLow = g_prevLow = 0;
      g_trendState = 0;
      g_lastBosUpH = g_lastBosDnH = -999999;
      g_bosUpPrev = g_bosDnPrev = false;
      g_cHigh = g_cPrevHigh = g_cLow = g_cPrevLow = 0;
      ArrayResize(g_orders, 0);
     }

   int start = MathMax(prev_calculated - 1, MathMax(InpPivotLtf, InpPivotHtf) * 2 + 15);

   for(int bar = start; bar < rates_total - 1; bar++)   // closed bars only
     {
      if(bar <= lastProcessed)
         continue;
      lastProcessed = bar;

      BufBuy[bar] = EMPTY_VALUE;  BufSell[bar]    = EMPTY_VALUE;
      BufPO2Buy[bar] = EMPTY_VALUE; BufPO2Sell[bar] = EMPTY_VALUE;
      if(bar + 1 < rates_total)
        {
         BufBuy[bar + 1] = EMPTY_VALUE;  BufSell[bar + 1]    = EMPTY_VALUE;
         BufPO2Buy[bar + 1] = EMPTY_VALUE; BufPO2Sell[bar + 1] = EMPTY_VALUE;
        }

      double atr = atrBuf[bar];
      if(atr <= 0.0)
         continue;
      bool live = (bar >= rates_total - 2);   // only alert on the latest closed bar

      //--- advance the analysis-timeframe cursor (merge join, no repaint) ----
      if(hIdx < 0)
        {
         // find the first analysis bar that is already closed at this chart bar
         int j = 0;
         while(j + 1 < hCount && htf[j + 1].time <= time[bar])
            j++;
         if(j < InpPivotHtf * 2 + 1)
            continue;                        // not enough history yet
         hIdx = j;
        }
      if(!InpSingleTf)
         while(hIdx + 1 < hCount && htf[hIdx + 1].time <= time[bar])
           {
            hIdx++;
            ProcessHtfBar(hIdx, htf, atrH, hCount);
           }

      double atrA = InpSingleTf ? atr
                    : (atrH[hIdx] > 0.0 ? atrH[hIdx] : atr);

      // With no analysis timeframe there is no analysis structure either, so
      // the Break of Structure that turns the trend is read from the CHART's
      // own last confirmed swings.
      if(InpSingleTf)
        {
         double cc = close[bar];
         bool bUp = (g_cHigh > 0 && cc > g_cHigh + atr * 0.1);
         bool bDn = (g_cLow  > 0 && cc < g_cLow  - atr * 0.1);
         if(bUp && !g_bosUpPrev) { g_lastBosUpH = bar; g_trendState =  1; }
         if(bDn && !g_bosDnPrev) { g_lastBosDnH = bar; g_trendState = -1; }
         g_bosUpPrev = bUp;
         g_bosDnPrev = bDn;
        }

      //--- CHART-timeframe zones (book p.41: zones are marked on the analysis
      //    timeframe AND on the chart; TP1 comes from a chart zone) ---------
      int pc = bar - InpPivotLtf;
      if(pc >= InpPivotLtf)
        {
         bool isPH = true, isPL = true;
         for(int k = pc - InpPivotLtf; k <= pc + InpPivotLtf; k++)
           {
            if(k == pc) continue;
            if(high[k] >= high[pc]) isPH = false;
            if(low[k]  <= low[pc])  isPL = false;
            if(!isPH && !isPL) break;
           }
         double hiRunC = high[pc], loRunC = low[pc];
         for(int k = pc; k <= bar; k++)
           {
            hiRunC = MathMax(hiRunC, high[k]);
            loRunC = MathMin(loRunC, low[k]);
           }
         double bodyHiC = MathMax(open[pc], close[pc]);
         double bodyLoC = MathMin(open[pc], close[pc]);
         double fHiC = 0.0, fLoC = 0.0, eTC = 0.0, eBC = 1.0, sLoC = 0.0, sHiC = 0.0;
         if(isPH)
           {
            g_cPrevHigh = g_cHigh;  g_cHigh = high[pc];
            ReanchorFlipped(high[pc], true, atr, false, pc, high, low);
            MateSeg(false, high[pc], pc, atr, high, low, fHiC, fLoC);
            EngulfPair(open, high, low, close, pc, true, rates_total, eTC, eBC);
            StructBand(open, high, low, close, pc, rates_total, true, sLoC, sHiC);
            AddSwingZone(false, high[pc], true, pc, bar, atr, bodyHiC, bodyLoC,
                         hiRunC, loRunC, close[bar], time[pc], time[bar],
                         fHiC, fLoC, eTC, eBC, MathAbs(close[pc] - open[pc]),
                         high[pc], low[pc], false, sLoC, sHiC);
           }
         if(isPL)
           {
            g_cPrevLow = g_cLow;    g_cLow = low[pc];
            ReanchorFlipped(low[pc], false, atr, false, pc, high, low);
            MateSeg(false, low[pc], pc, atr, high, low, fHiC, fLoC);
            EngulfPair(open, high, low, close, pc, false, rates_total, eTC, eBC);
            StructBand(open, high, low, close, pc, rates_total, false, sLoC, sHiC);
            AddSwingZone(false, low[pc], false, pc, bar, atr, bodyHiC, bodyLoC,
                         hiRunC, loRunC, close[bar], time[pc], time[bar],
                         fHiC, fLoC, eTC, eBC, MathAbs(close[pc] - open[pc]),
                         high[pc], low[pc], false, sLoC, sHiC);
           }

         //--- the LINE CHART's own zones: pivots of CLOSES, no wicks at all.
         //    Image 31 says to use a line chart to mark the zones better, and
         //    images 12/13 print the same chart twice with the level sitting
         //    on the CLOSE level while the wicks poke through it.
         if(InpLineZones)
           {
            bool lPH = true, lPL = true;
            for(int k = pc - InpPivotLtf; k <= pc + InpPivotLtf; k++)
              {
               if(k == pc) continue;
               if(close[k] >= close[pc]) lPH = false;
               if(close[k] <= close[pc]) lPL = false;
               if(!lPH && !lPL) break;
              }
            if(lPH || lPL)
              {
               double hiRunL = close[pc], loRunL = close[pc];
               for(int k = pc; k <= bar; k++)
                 {
                  hiRunL = MathMax(hiRunL, close[k]);
                  loRunL = MathMin(loRunL, close[k]);
                 }
               double lHi = 0.0, lLo = 0.0;
               if(lPH)
                 {
                  MateSeg(false, close[pc], pc, atr, close, close, lHi, lLo, true);
                  AddSwingZone(false, close[pc], true, pc, bar, atr, bodyHiC, bodyLoC,
                               hiRunL, loRunL, close[bar], time[pc], time[bar],
                               lHi, lLo, 0.0, 1.0, 0.0, 0.0, 0.0, true);
                 }
               if(lPL)
                 {
                  MateSeg(false, close[pc], pc, atr, close, close, lHi, lLo, true);
                  AddSwingZone(false, close[pc], false, pc, bar, atr, bodyHiC, bodyLoC,
                               hiRunL, loRunL, close[bar], time[pc], time[bar],
                               lHi, lLo, 0.0, 1.0, 0.0, 0.0, 0.0, true);
                 }
              }
           }
        }

      //--- a zone dies when the market BREAKS it, not when a clock runs out.
      //    The book never retires a level by age; this is off by default.
      if(InpExpireByAge)
         for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
            if(!g_zones[i].htf && bar - g_zones[i].bornH > InpLifeLtf)
               RemoveZoneAt(i);

      // both sides of structure broken recently = sideway. "No Setup, No Trade".
      // g_lastBos* holds an ANALYSIS index normally and a CHART index under
      // InpSingleTf, so the cursor compared against it has to match.
      int rIdx = InpSingleTf ? bar : hIdx;
      bool inRange   = (rIdx - g_lastBosUpH) <= InpRangeBars &&
                       (rIdx - g_lastBosDnH) <= InpRangeBars;
      // Until the analysis timeframe has printed two swings it has NO opinion,
      // and with g_trendState stuck at 0 the filter blocked every trade
      // forever. "Trend is King" does not mean "no opinion, no trade" — the
      // chart's own last two swings answer instead.
      int effTrend = g_trendState;
      if(effTrend == 0 && g_cPrevHigh > 0 && g_cPrevLow > 0)
        {
         if(g_cHigh > g_cPrevHigh && g_cLow > g_cPrevLow)      effTrend = 1;
         else if(g_cHigh < g_cPrevHigh && g_cLow < g_cPrevLow) effTrend = -1;
        }
      bool trendUp      = (effTrend ==  1 && !inRange);
      bool trendDown    = (effTrend == -1 && !inRange);
      bool trendUnknown = (effTrend ==  0 && !inRange);

      // The limit-order model needs no confirmation candle: the book puts the
      // order ON the zone before price arrives, and the fill IS the entry.
      double o = open[bar], h = high[bar], l = low[bar], c = close[bar];
      // p25: "it MUST break TWO supports; if it breaks more, that is no
      // problem" - so what is counted is LEVELS, not bars. These were plain
      // booleans, so one impulse through three resistances credited a support
      // with a single break. The prices are collected rather than counted,
      // because the same real level is usually carried by several zones at
      // once (candle pass, line pass, analysis pass) and Overlaps() only keeps
      // them apart WITHIN a set.
      double brokeSupPx[], brokeResPx[];
      ArrayResize(brokeSupPx, 0);
      ArrayResize(brokeResPx, 0);

      //--- fill the resting orders, manage the live trades ------------------
      for(int i = ArraySize(g_orders) - 1; i >= 0; i--)
        {
         if(g_orders[i].stat == -9)
           {
            // a limit can only fill on a bar AFTER it was placed
            if(bar <= g_orders[i].bar)
               continue;
            if(l <= g_orders[i].entry && h >= g_orders[i].entry)
              {
               g_orders[i].stat = 0;
               g_orders[i].bar  = bar;
               g_orders[i].time = time[bar];
              }
            else
              {
               bool blown = g_orders[i].buy ? (l <= g_orders[i].sl) : (h >= g_orders[i].sl);
               bool gone  = g_orders[i].buy ? (h >= g_orders[i].tp1) : (l <= g_orders[i].tp1);
               if(blown || gone || bar - g_orders[i].bar > InpOrderExpiry)
                  ArrayRemove(g_orders, i, 1);
              }
            continue;
           }
         if(g_orders[i].stat != 0)
            continue;

         // On the FILL bar only the stop may be judged. A buy limit fills
         // because price traded DOWN to it, so a low beyond the stop came
         // after the fill — but the bar's high may have printed before it.
         bool entryBar = (bar == g_orders[i].bar);
         if(g_orders[i].buy)
           {
            if(l <= g_orders[i].sl)          g_orders[i].stat = g_orders[i].be ? -2 : -1;
            else if(entryBar)                { }
            else if(h >= g_orders[i].tp3)    g_orders[i].stat = 3;
            else if(h >= g_orders[i].tp2 && g_orders[i].stat < 2) g_orders[i].stat = 2;
            else if(h >= g_orders[i].tp1 && g_orders[i].stat < 1) g_orders[i].stat = 1;
           }
         else
           {
            if(h >= g_orders[i].sl)          g_orders[i].stat = g_orders[i].be ? -2 : -1;
            else if(entryBar)                { }
            else if(l <= g_orders[i].tp3)    g_orders[i].stat = 3;
            else if(l <= g_orders[i].tp2 && g_orders[i].stat < 2) g_orders[i].stat = 2;
            else if(l <= g_orders[i].tp1 && g_orders[i].stat < 1) g_orders[i].stat = 1;
           }
         // image 41: the red 1:1 line is where the stop goes to entry and
         // money comes off - not the first zone target, which can be far
         if(InpBreakEven && !g_orders[i].be && !entryBar)
           {
            double r1 = g_orders[i].buy
                        ? g_orders[i].entry + g_orders[i].risk0 * InpBeAtR
                        : g_orders[i].entry - g_orders[i].risk0 * InpBeAtR;
            bool hit = g_orders[i].buy ? (h >= r1) : (l <= r1);
            if(hit || g_orders[i].stat >= 1)
              {
               g_orders[i].sl = g_orders[i].entry;
               g_orders[i].be = true;
              }
           }
         if(g_orders[i].stat == 0 && bar - g_orders[i].bar > InpMaxTradeBars)
            g_orders[i].stat = -3;               // timed out, closed flat
         // a zone that stopped a trade out has proved it is not respected
         if(InpKillOnStop && g_orders[i].stat == -1)
            for(int k = 0; k < ArraySize(g_zones); k++)
               if(g_zones[k].id == g_orders[i].uid)
                  g_zones[k].dead = true;
        }

      // A Weekly/Daily zone can be years old and hundreds of points away. It is
      // not tradeable any more and it wrecks the chart scale, so drop it.
      //    A level price has not reached yet is exactly what a running trade
      //    is aiming AT, so distance only HIDES the box now.
      for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
        {
         double ref = g_zones[i].htf ? atrA : atr;
         double gap = c > g_zones[i].top ? c - g_zones[i].top
                      : (c < g_zones[i].bot ? g_zones[i].bot - c : 0.0);
         g_zones[i].far = (gap > ref * InpMaxZoneDistATR);
         if(InpDropFar && g_zones[i].far)
            RemoveZoneAt(i);
        }

      // every zone carries its own order, so nothing blocks anything: the
      // only limit is one live order per zone and a cap on the total
      bool canFire = LiveCount() < InpMaxOpen;

      // liquidity sweep guard (book): gold takes the sell-side liquidity first
      // and then moves, so a sell right after a fresh low is selling the
      // reversal. On the real H4 data the old code sold 4 bars after the 3942
      // bottom and the market ran 600 points the other way.
      bool sweptLow = false, sweptHigh = false;
      if(InpSweepGuard && bar >= InpSweepBars)
        {
         double loW = low[bar], hiW = high[bar];
         for(int k = bar - InpSweepBars + 1; k <= bar; k++)
           {
            loW = MathMin(loW, low[k]);
            hiW = MathMax(hiW, high[k]);
           }
         double loR = low[bar], hiR = high[bar];
         for(int k = MathMax(0, bar - InpSweepRecent + 1); k <= bar; k++)
           {
            loR = MathMin(loR, low[k]);
            hiR = MathMax(hiR, high[k]);
           }
         sweptLow  = (loR <= loW);
         sweptHigh = (hiR >= hiW);
        }

      //--- zone engine -----------------------------------------------------
      for(int i = 0; i < ArraySize(g_zones); i++)
        {
         if(time[bar] <= g_zones[i].activeFrom)
            continue;
         bool inZone = (l <= g_zones[i].top && h >= g_zones[i].bot);

         if(g_zones[i].role == 1)   // SUPPORT
           {
            if(g_zones[i].pendDir == 0 && BearBreak75(g_zones[i].bot, o, h, l, c))
              {
               g_zones[i].pendBar = bar;      // wait — does the break HOLD?
               g_zones[i].pendDir = -1;
              }
            else if(g_zones[i].pendDir == -1)
              {
               if(c >= g_zones[i].bot)        // came back: p47 False Breakout Area
                 {
                  g_zones[i].pendBar = -1;
                  g_zones[i].pendDir = 0;
                  // 2026 master class, "FALSE BREAKOUT AREA": the area the
                  // market has broken TWICE and then respected again. One
                  // failed break is not an FBA - it takes two.
                  g_zones[i].falseBreaks++;
                  g_zones[i].fba     = (g_zones[i].falseBreaks >= 2);
                  g_zones[i].dead    = false;
                 }
               else if(bar - g_zones[i].pendBar >= InpFbaBars)
                 {
                  g_zones[i].wasValid = (g_zones[i].state == 1);
                  g_zones[i].role  = -1;
                  g_zones[i].state = 2;
                  g_zones[i].awaitPull = InpFlipNeedsPullback ? bar : -1;
                  g_zones[i].touches  = 0;
                  g_zones[i].sigTouch = 0;
                  g_zones[i].srr      = false;
                  g_zones[i].fba      = false;
                  g_zones[i].falseBreaks = 0;
                  g_zones[i].flips++;
                  // a level broken from both sides repeatedly is a range boundary
                  g_zones[i].dead     = (g_zones[i].flips >= InpMaxFlips);
                  g_zones[i].pendBar  = -1;
                  g_zones[i].pendDir  = 0;
                  {
                   int nb = ArraySize(brokeSupPx);
                   ArrayResize(brokeSupPx, nb + 1);
                   brokeSupPx[nb] = g_zones[i].bot;
                  }
                  Notify((g_zones[i].wasValid ? "I.VS" : "SBR") + " — Support broken (75% rule), zone inverted to SELL", live);
                 }
              }
            else if(inZone && c >= g_zones[i].bot && !g_zones[i].dead)
              {
               if(!g_zones[i].inZonePrev)
                 {
                  g_zones[i].touches++;
                  if(g_zones[i].state == 0 && g_zones[i].touches >= 2)
                     g_zones[i].state = 1;   // Second Movement → VALID
                  if((g_zones[i].state != 2 && g_zones[i].touches > InpMaxTouches) ||
                     (g_zones[i].state == 2 && g_zones[i].touches > 2))
                     g_zones[i].dead = true; // 3-touch rule: zone exhausted
                 }
               // tradable only: VALID (touch>=2), SRR, or INVERSION (touch 1-2)
               // p24/p35: a paired zone was drawn FROM two touches, so the
               // RETURN to it is already the entry — it does not need a third.
               int needT = g_zones[i].paired ? 1 : 2;
               bool tradable = !g_zones[i].dead && g_zones[i].pendDir == 0 &&
                               g_zones[i].awaitPull < 0 &&
                               ((g_zones[i].state == 1 && g_zones[i].touches >= needT) ||
                                g_zones[i].srr ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2)) &&
                               (!InpEntriesHtfOnly || g_zones[i].htf) &&
                               (!InpRequireNested  || ZoneNested(i));
               bool okTrend = (!InpTrendFilter || trendUp || trendUnknown ||
                               (InpAllowCounterInv && g_zones[i].state == 2)) && !sweptHigh;
               // one order per zone, placed while price is still ABOVE it and
               // aimed at the NEXT zone up. The book puts a LIMIT on the zone
               // BEFORE price gets there — no rejection candle is waited for.
               if(tradable && okTrend && canFire && !HasOrder(g_zones[i].id) && c > g_zones[i].top)
                 {
                  double swingLo = MathMin(MathMin(low[bar], low[bar - 1]), low[bar - 2]);
                  double zAtr    = g_zones[i].htf ? atrA : atr;
                  double entry   = g_zones[i].top;
                  double rawSl   = MathMin(g_zones[i].bot, swingLo) - zAtr * InpSlBufferATR;
                  double risk    = MathMax(MathAbs(entry - rawSl), atr * InpMinSlATR);
                  bool   tooWide = (InpMaxSlATR > 0.0 && risk > atr * InpMaxSlATR);
                  double t1 = NextZone(true, entry, 0);
                  if(t1 > 0.0 && !tooWide && (t1 - entry) >= risk * InpMinRR)
                    {
                     double t2 = NextZone(true, entry, 1);
                     double t3 = NextZone(true, entry, 2);
                     // Every target is a zone that is ALREADY on the chart
                     // and still unbroken. No second zone ahead means no TP2:
                     // the whole position comes off at TP1.
                     if(t2 <= 0.0) t2 = t1;
                     if(t3 <= 0.0) t3 = t2;
                     bool isPO2 = (g_zones[i].state == 2 && g_zones[i].touches == 1); // image 55
                     if(isPO2)
                       {
                        BufPO2Buy[bar] = l - atr * 0.4;
                        Notify("PO2 BUY limit at " + ZoneText(g_zones[i]), live);
                       }
                     else
                       {
                        BufBuy[bar] = l - atr * 0.3;
                        Notify("BUY limit at " + ZoneText(g_zones[i]), live);
                       }
                     PushOrder(true, isPO2, g_zones[i].id, bar, time[bar], entry,
                               entry - risk, t1, t2, t3, ZoneText(g_zones[i]),
                               risk, PeriodSeconds(_Period) >= 3600);
                    }
                 }
              }
           }
         else                        // RESISTANCE
           {
            if(g_zones[i].pendDir == 0 && BullBreak75(g_zones[i].top, o, h, l, c))
              {
               g_zones[i].pendBar = bar;
               g_zones[i].pendDir = 1;
              }
            else if(g_zones[i].pendDir == 1)
              {
               if(c <= g_zones[i].top)        // came back: p47 False Breakout Area
                 {
                  g_zones[i].pendBar = -1;
                  g_zones[i].pendDir = 0;
                  // 2026 master class, "FALSE BREAKOUT AREA": the area the
                  // market has broken TWICE and then respected again. One
                  // failed break is not an FBA - it takes two.
                  g_zones[i].falseBreaks++;
                  g_zones[i].fba     = (g_zones[i].falseBreaks >= 2);
                  g_zones[i].dead    = false;
                 }
               else if(bar - g_zones[i].pendBar >= InpFbaBars)
                 {
                  g_zones[i].wasValid = (g_zones[i].state == 1);
                  g_zones[i].role  = 1;
                  g_zones[i].state = 2;
                  g_zones[i].awaitPull = InpFlipNeedsPullback ? bar : -1;
                  g_zones[i].touches  = 0;
                  g_zones[i].sigTouch = 0;
                  g_zones[i].srr      = false;
                  g_zones[i].fba      = false;
                  g_zones[i].falseBreaks = 0;
                  g_zones[i].flips++;
                  g_zones[i].dead     = (g_zones[i].flips >= InpMaxFlips);
                  g_zones[i].pendBar  = -1;
                  g_zones[i].pendDir  = 0;
                  {
                   int nb = ArraySize(brokeResPx);
                   ArrayResize(brokeResPx, nb + 1);
                   brokeResPx[nb] = g_zones[i].top;
                  }
                  Notify((g_zones[i].wasValid ? "I.VR" : "RBS") + " — Resistance broken (75% rule), zone inverted to BUY", live);
                 }
              }
            else if(inZone && c <= g_zones[i].top && !g_zones[i].dead)
              {
               if(!g_zones[i].inZonePrev)
                 {
                  g_zones[i].touches++;
                  if(g_zones[i].state == 0 && g_zones[i].touches >= 2)
                     g_zones[i].state = 1;
                  if((g_zones[i].state != 2 && g_zones[i].touches > InpMaxTouches) ||
                     (g_zones[i].state == 2 && g_zones[i].touches > 2))
                     g_zones[i].dead = true;
                 }
               // p24/p35: a paired zone was drawn FROM two touches, so the
               // RETURN to it is already the entry — it does not need a third.
               int needT = g_zones[i].paired ? 1 : 2;
               bool tradable = !g_zones[i].dead && g_zones[i].pendDir == 0 &&
                               g_zones[i].awaitPull < 0 &&
                               ((g_zones[i].state == 1 && g_zones[i].touches >= needT) ||
                                g_zones[i].srr ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2)) &&
                               (!InpEntriesHtfOnly || g_zones[i].htf) &&
                               (!InpRequireNested  || ZoneNested(i));
               bool okTrend = (!InpTrendFilter || trendDown || trendUnknown ||
                               (InpAllowCounterInv && g_zones[i].state == 2)) && !sweptLow;
               if(tradable && okTrend && canFire && !HasOrder(g_zones[i].id) && c < g_zones[i].bot)
                 {
                  double swingHi = MathMax(MathMax(high[bar], high[bar - 1]), high[bar - 2]);
                  double zAtr    = g_zones[i].htf ? atrA : atr;
                  double entry   = g_zones[i].bot;
                  double rawSl   = MathMax(g_zones[i].top, swingHi) + zAtr * InpSlBufferATR;
                  double risk    = MathMax(MathAbs(rawSl - entry), atr * InpMinSlATR);
                  bool   tooWide = (InpMaxSlATR > 0.0 && risk > atr * InpMaxSlATR);
                  double t1 = NextZone(false, entry, 0);
                  if(t1 > 0.0 && !tooWide && (entry - t1) >= risk * InpMinRR)
                    {
                     double t2 = NextZone(false, entry, 1);
                     double t3 = NextZone(false, entry, 2);
                     // same rule on the sell side: no zone, no target
                     if(t2 <= 0.0) t2 = t1;
                     if(t3 <= 0.0) t3 = t2;
                     bool isPO2 = (g_zones[i].state == 2 && g_zones[i].touches == 1); // image 55
                     if(isPO2)
                       {
                        BufPO2Sell[bar] = h + atr * 0.4;
                        Notify("PO2 SELL limit at " + ZoneText(g_zones[i]), live);
                       }
                     else
                       {
                        BufSell[bar] = h + atr * 0.3;
                        Notify("SELL limit at " + ZoneText(g_zones[i]), live);
                       }
                     PushOrder(false, isPO2, g_zones[i].id, bar, time[bar], entry,
                               entry + risk, t1, t2, t3, ZoneText(g_zones[i]),
                               risk, PeriodSeconds(_Period) >= 3600);
                    }
                 }
              }
           }
         g_zones[i].inZonePrev = inZone;
         DrawZone(g_zones[i], g_zones[i].bornTime, time[bar]);
        }

      //--- SRR / RSS qualification (book): Support that broke >=2 Resistances
      //    becomes SRR (buy); Resistance that broke >=2 Supports becomes RSS.
      int nSup = DistinctLevels(brokeSupPx, atr);
      int nRes = DistinctLevels(brokeResPx, atr);
      if(nSup > 0 || nRes > 0)
        {
         for(int i = 0; i < ArraySize(g_zones); i++)
           {
            if(g_zones[i].state == 2 || g_zones[i].dead)
               continue;
            if(nRes > 0 && g_zones[i].role == 1 && c > g_zones[i].top)
              {
               g_zones[i].oppBreaks += nRes;
               if(g_zones[i].oppBreaks >= 2)
                  g_zones[i].srr = true;
              }
            if(nSup > 0 && g_zones[i].role == -1 && c < g_zones[i].bot)
              {
               g_zones[i].oppBreaks += nSup;
               if(g_zones[i].oppBreaks >= 2)
                  g_zones[i].srr = true;
              }
           }
        }
     }

   DrawPosition(time[rates_total - 1]);
   ChartRedraw();
   return rates_total;
  }
//+------------------------------------------------------------------+
