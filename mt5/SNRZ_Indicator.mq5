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
#property version     "7.40"
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
input double InpBreakoutPct  = 75.0;  // Breakout rule (%) — the 75% rule
input double InpMinZoneATR   = 0.15;  // Min zone height (ATR x)
input double InpMaxZoneATR   = 0.40;  // Max zone height (ATR x)
input int    InpLifeLtf      = 600;   // Chart zone lifetime (chart bars)
input int    InpLifeHtf      = 150;    // Analysis zone lifetime (analysis bars)
input double InpMaxZoneDistATR = 6.0; // Drop zones further than (ATR x)
input bool   InpPairZones     = true;  // Draw a zone only from TWO swings (p24: S+S/R+R/S+R/R+S)
input double InpPairTolATR   = 0.50;  // ..."similar price" = closer than (ATR x)
input int    InpPairMaxGap   = 90;    // ...and the two swings closer than N bars
input int    InpPairLookback = 10;     // ...searching the last N swings
input int    InpFbaBars      = 3;     // A break must hold N bars before the zone inverts (p47)

input group "Signals"
input bool   InpTrendFilter  = true;  // Trade only with structure trend
input bool   InpAllowCounterInv = false; // Allow counter-trend entries on inversion zones
input bool   InpNeedConfirm  = true;  // Require confirmation candle
input int    InpMaxTouches   = 3;     // Max touches per zone (3-touch rule)
input int    InpMaxFlips     = 2;     // Max role flips before a zone is finished
input bool   InpKillOnStop   = true;  // A zone that got stopped out is finished
input bool   InpNeedMicroBos = true;  // Confirmation must break the last bars' structure
input int    InpMicroBosLen  = 2;     // ...over the last N bars (higher = stricter)
input bool   InpBreakEven    = true;  // Move stop to entry once TP1 is reached
input bool   InpNeedReject   = true;  // Confirmation candle must close OUTSIDE the zone
input int    InpRangeBars    = 10;    // Range lockout (analysis-TF bars since opposite BOS)
input bool   InpOneTrade     = true;  // One trade at a time (no overtrade)
input int    InpMaxTradeBars = 120;   // Close an open trade after N chart bars
input double InpMinSlATR     = 4.0;   // Minimum stop distance (ATR x) — the book puts the stop ON the liquidity
input double InpTpMaxR       = 6.0;   // Max R for TP1/TP2 (farther zone -> TP3)
input bool   InpEntryAtZone   = true;  // Entry = LIMIT order on the zone (p41/p42)
input bool   InpEntryEdge     = true;  // ...at the near EDGE of the zone, not its middle
input double InpSlBufferATR   = 2.0;   // How far beyond the zone the stop sits (ATR x)
input int    InpOrderExpiry   = 10;    // An unfilled limit order dies after N bars
input bool   InpRequireNested = false; // Only chart zones sitting inside an analysis zone (p14)
input bool   InpEntriesHtfOnly= false; // Entries only from analysis-timeframe zones
input double InpRrTp1         = 1.0;   // TP1 = this many times the stop distance
// Win rate is not a quality of the strategy, it is a CHOICE of where the stop
// and the first target sit. Measured on 83 days of real XAUUSD:
//   stop  4 ATR - TP1 1.00R -> 54% win, E -0.02R   (the default)
//   stop  8 ATR - TP1 0.25R -> 79% win, E +0.01R
//   stop 20 ATR - TP1 0.10R -> 92% win, E -0.02R   (this switch)
// Expectancy barely moves across all of it. 92% wins means risking about
// $200 to make about $20 on M15 gold: one loss erases ten wins.
input bool   InpHighWinRate   = true;  // High win-rate mode (~92%) — read the note above
input bool   InpShowPosition = true;  // Draw Entry / SL / TP1-3 of the last setup

input group "Alerts"
input bool   InpAlertPopup   = true;  // Alert window
input bool   InpAlertPush    = false; // Push notification to phone

input group "Style"
input color  InpColSup       = C'8,153,129';   // Support zone
input color  InpColRes       = C'242,54,69';   // Resistance zone
input color  InpColInv       = C'212,175,55';  // Inversion zone (Zindan gold)
input uchar  InpFillAlpha    = 40;             // (reserved)

//--- the win-rate dial, applied --------------------------------------------
double EffMinSl()  { return InpHighWinRate ? 16.0 : InpMinSlATR;    }
double EffSlBuf()  { return InpHighWinRate ?  5.0 : InpSlBufferATR; }
double EffRrTp1()  { return InpHighWinRate ? 0.10 : InpRrTp1;       }

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
   bool     fba;       // broken and then respected again (p47)
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
bool     g_posOn = false, g_posBuy = false, g_posPO2 = false, g_posSwing = false;
double   g_posEntry = 0, g_posSL = 0, g_posTP1 = 0, g_posTP2 = 0, g_posTP3 = 0;
int      g_posStat = 0;                   // 0 running · 1/2/3 TP · -1 stop · -2 BE

//--- resting LIMIT order (book p41/p42) --------------------------------------
// The entry is an order sitting AT the zone. It is armed when the confirmation
// candle closes and can only fill on a LATER bar — filling it on the
// confirmation bar itself would be reading the future, since the signal is not
// known until that bar has closed and by then price has already left the zone.
bool     g_ordOn = false, g_ordBuy = false, g_ordPO2 = false;
double   g_ordEntry = 0, g_ordSL = 0, g_ordTP1 = 0, g_ordTP2 = 0, g_ordTP3 = 0;
int      g_ordBar = 0;
long     g_ordUid = -1;
string   g_ordZone = "";
bool     g_posBE   = false;               // stop already moved to entry
long     g_posUid  = -1;                  // which zone produced this setup
int      g_posBar  = 0;
datetime g_posTime = 0;
string   g_posZone = "";

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
   int m = PeriodSeconds(_Period) / 60;
   if(m <= 5)    return PERIOD_M15;
   if(m <= 30)   return PERIOD_H1;
   if(m <= 60)   return PERIOD_H4;
   if(m <= 240)  return PERIOD_D1;
   if(m <= 1440) return PERIOD_W1;
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

void DrawZone(const SZone &z, const datetime t1, const datetime t2)
  {
   string nm = ZoneName(z.id);
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
   ObjectSetString (0, ln, OBJPROP_TEXT, ZoneText(z));
   ObjectSetInteger(0, ln, OBJPROP_COLOR, ZoneColor(z));
  }

void DeleteZone(const SZone &z)
  {
   ObjectDelete(0, ZoneName(z.id));
   ObjectDelete(0, ZoneLblName(z.id));
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
  };
SSwing g_swLtf[], g_swHtf[];

void PushSwing(const bool htf, const double price, const bool isHigh, const int bar)
  {
   int n;
   if(htf)
     {
      n = ArraySize(g_swHtf);
      ArrayResize(g_swHtf, n + 1);
      g_swHtf[n].price = price; g_swHtf[n].isHigh = isHigh; g_swHtf[n].bar = bar;
      if(ArraySize(g_swHtf) > 40) ArrayRemove(g_swHtf, 0, 1);
     }
   else
     {
      n = ArraySize(g_swLtf);
      ArrayResize(g_swLtf, n + 1);
      g_swLtf[n].price = price; g_swLtf[n].isHigh = isHigh; g_swLtf[n].bar = bar;
      if(ArraySize(g_swLtf) > 40) ArrayRemove(g_swLtf, 0, 1);
     }
  }

// index of the newest earlier swing at a similar price, or -1
int FindMate(const bool htf, const double price, const int bar, const double atr)
  {
   double tol = atr * InpPairTolATR;
   int n = htf ? ArraySize(g_swHtf) : ArraySize(g_swLtf);
   int first = MathMax(0, n - InpPairLookback);
   for(int i = n - 1; i >= first; i--)
     {
      int    sb = htf ? g_swHtf[i].bar   : g_swLtf[i].bar;
      double sp = htf ? g_swHtf[i].price : g_swLtf[i].price;
      if(bar - sb <= InpPairMaxGap && MathAbs(sp - price) <= tol)
         return i;
     }
   return -1;
  }

void AddZone(double top, double bot, const int role, const int bornH, const double atr,

             const datetime t1, const datetime t2, const bool htf, const bool paired)
  {
   double h  = top - bot;
   double mn = atr * InpMinZoneATR;
   double mx = atr * InpMaxZoneATR;
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
//| One confirmed swing -> at most one zone, drawn with its mate      |
//+------------------------------------------------------------------+
void AddSwingZone(const bool htf, const double price, const bool isHigh,
                  const int bar, const int bornH, const double atr,
                  const double bodyHi, const double bodyLo,
                  const double hiRun, const double loRun,
                  const double refClose, const datetime t1, const datetime t2)
  {
   int mi = InpPairZones ? FindMate(htf, price, bar, atr) : -1;
   double matePrice = 0.0;
   bool   mateHigh  = false;
   if(mi >= 0)
     {
      matePrice = htf ? g_swHtf[mi].price  : g_swLtf[mi].price;
      mateHigh  = htf ? g_swHtf[mi].isHigh : g_swLtf[mi].isHigh;
     }
   PushSwing(htf, price, isHigh, bar);

   if(mi < 0)
     {
      // p24, the FIFTH way to draw a zone: "when there is no S/R pair to draw
      // from, draw it from the engulf". The book never says "then draw
      // nothing" — and without this the panel read Zones: 0. An unpaired zone
      // is born FRESH, so it still needs its two touches before it trades.
      double bigM = atr * InpBigMoveATR;
      if(isHigh)
        {
         if((price - loRun) >= bigM && !Overlaps(price, bodyHi, htf))
            AddZone(price, bodyHi, -1, bornH, atr, t1, t2, htf, false);
        }
      else
        {
         if((hiRun - price) >= bigM && !Overlaps(bodyLo, price, htf))
            AddZone(bodyLo, price, 1, bornH, atr, t1, t2, htf, false);
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
   if(!Overlaps(t, b, htf))
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

void DrawPosition(const datetime tNow)
  {
   if(!InpShowPosition || g_posTime == 0)
      return;
   datetime t2 = tNow + (datetime)(PeriodSeconds(_Period) * 25);
   color dirCol = g_posBuy ? clrMediumSeaGreen : clrTomato;
   color head   = g_posPO2 ? InpColInv : dirCol;
   int   st     = g_posOn ? STYLE_SOLID : STYLE_DOT;
   string kind  = g_posSwing ? "SWING" : "SCALP";
   string dir   = g_posBuy ? "BUY" : "SELL";
   string stat  = g_posStat == -2 ? "  = TP1 -> BE" :
                  g_posStat == -1 ? "  x SL" :
                  g_posStat == 3  ? "  v TP3" :
                  g_posStat == 2  ? "  v TP2" :
                  g_posStat == 1  ? "  v TP1" : (g_posOn ? "  . running" : "  . closed");

   PosLine("E",  g_posTime, t2, g_posEntry, clrWhite,           2, STYLE_DOT);
   PosLine("SL", g_posTime, t2, g_posSL,    clrTomato,          2, st);
   PosLine("T1", g_posTime, t2, g_posTP1,   clrMediumSeaGreen,  2, st);
   // on a FINISHED trade only the levels it actually reached stay drawn
   if(g_posOn || g_posStat >= 2)
      PosLine("T2", g_posTime, t2, g_posTP2, clrMediumSeaGreen, 1, STYLE_DASH);
   else
     {
      ObjectDelete(0, g_prefix + "P_T2");
      ObjectDelete(0, g_prefix + "T_T2");
     }
   if(g_posOn || g_posStat >= 3)
      PosLine("T3", g_posTime, t2, g_posTP3, clrMediumSeaGreen, 1, STYLE_DASH);
   else
     {
      ObjectDelete(0, g_prefix + "P_T3");
      ObjectDelete(0, g_prefix + "T_T3");
     }

   PosText("E",  t2, g_posEntry, dir + " " + kind + (g_posPO2 ? " PO2" : "") + " " +
           g_posZone + stat + "  " + DoubleToString(g_posEntry, _Digits), head);
   PosText("SL", t2, g_posSL,  (g_posBE ? "SL-BE " : "SL  ") + DoubleToString(g_posSL, _Digits), clrTomato);
   PosText("T1", t2, g_posTP1, "TP1 " + DoubleToString(g_posTP1, _Digits), clrMediumSeaGreen);
   if(g_posOn || g_posStat >= 2)
      PosText("T2", t2, g_posTP2, "TP2 " + DoubleToString(g_posTP2, _Digits), clrMediumSeaGreen);
   if(g_posOn || g_posStat >= 3)
      PosText("T3", t2, g_posTP3, "TP3 " + DoubleToString(g_posTP3, _Digits), clrMediumSeaGreen);
  }
//+------------------------------------------------------------------+
//| Targets: nearest opposite zones ahead of price, else 1R/2R/3R     |
//+------------------------------------------------------------------+
void BuildTargets(const bool isBuy, const double entry, const double risk,
                  double &t1, double &t2, double &t3)
  {
   // Book p.44: "TP1 from the 5m timeframe, TP2 from the 1h timeframe" — so
   // the first target is the nearest opposite CHART zone and the second is the
   // nearest opposite ANALYSIS zone. 1R/2R/3R when no zone sits there.
   double cap = risk * InpTpMaxR;
   double d1 = -1, d2 = -1, d3 = -1;
   for(int i = 0; i < ArraySize(g_zones); i++)
     {
      if(g_zones[i].dead)
         continue;
      double lvl = isBuy ? g_zones[i].bot : g_zones[i].top;
      bool ahead = isBuy ? (g_zones[i].role == -1 && lvl > entry)
                   : (g_zones[i].role ==  1 && lvl < entry);
      if(!ahead)
         continue;
      double d = MathAbs(lvl - entry);
      if(!g_zones[i].htf && d <= cap && (d1 < 0 || d < d1))
         d1 = d;
      if(g_zones[i].htf && (d2 < 0 || d < d2))
         d2 = d;
      if(d3 < 0 || d > d3)
         d3 = d;
     }
   d1 = (d1 < 0) ? risk : MathMax(d1, risk);
   d2 = (d2 < 0 || d2 <= d1) ? MathMax(d1 + risk, risk * 2.0) : d2;
   d3 = (d3 < 0 || d3 <= d2) ? MathMax(d2 + risk, risk * 3.0) : d3;
   // a zone can sit absurdly far away — on a 5m scalp that produced a TP2
   // 275 points from entry, which is not a target, it is a wish
   d2 = MathMax(MathMin(d2, cap),       d1 * 1.5);
   d3 = MathMax(MathMin(d3, cap * 1.5), d2 * 1.5);
   t1 = isBuy ? entry + d1 : entry - d1;
   t2 = isBuy ? entry + d2 : entry - d2;
   t3 = isBuy ? entry + d3 : entry - d3;
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
      if(isPL)
         AddSwingZone(true, htf[p].low, false, p, j, atr, bodyHiH, bodyLoH,
                      hiRun, loRun, htf[j].close, htf[p].time, htf[j].time);
      if(isPH)
         AddSwingZone(true, htf[p].high, true, p, j, atr, bodyHiH, bodyLoH,
                      hiRun, loRun, htf[j].close, htf[p].time, htf[j].time);
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

   // expire ANALYSIS zones by analysis-TF age (chart zones age on chart bars)
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
      g_posOn = false; g_posTime = 0; g_posStat = 0;
      g_ordOn = false;
      g_posBE = false; g_posUid = -1;
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
      while(hIdx + 1 < hCount && htf[hIdx + 1].time <= time[bar])
        {
         hIdx++;
         ProcessHtfBar(hIdx, htf, atrH, hCount);
        }

      double atrA = atrH[hIdx] > 0.0 ? atrH[hIdx] : atr;

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
         if(isPH)
           {
            g_cPrevHigh = g_cHigh;  g_cHigh = high[pc];
            AddSwingZone(false, high[pc], true, pc, bar, atr, bodyHiC, bodyLoC,
                         hiRunC, loRunC, close[bar], time[pc], time[bar]);
           }
         if(isPL)
           {
            g_cPrevLow = g_cLow;    g_cLow = low[pc];
            AddSwingZone(false, low[pc], false, pc, bar, atr, bodyHiC, bodyLoC,
                         hiRunC, loRunC, close[bar], time[pc], time[bar]);
           }
        }

      //--- expire chart zones by chart-bar age, and drop far ones -----------
      for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
         if(!g_zones[i].htf && bar - g_zones[i].bornH > InpLifeLtf)
            RemoveZoneAt(i);

      // both sides of structure broken recently = sideway. "No Setup, No Trade".
      bool inRange   = (hIdx - g_lastBosUpH) <= InpRangeBars &&
                       (hIdx - g_lastBosDnH) <= InpRangeBars;
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

      //--- confirmation candles (SNRZ style, on the CHART timeframe) --------
      double o = open[bar], h = high[bar], l = low[bar], c = close[bar];
      double o1 = open[bar - 1], c1 = close[bar - 1];
      bool bullEngulf = (c > o) && (c1 < o1) && (c >= o1);
      bool bearEngulf = (c < o) && (c1 > o1) && (c <= o1);
      double rng = h - l;
      bool bullPin = rng > 0 && (MathMin(o, c) - l) >= 0.6 * rng && c >= o;
      bool bearPin = rng > 0 && (h - MathMax(o, c)) >= 0.6 * rng && c <= o;
      bool bullConfirm = bullEngulf || bullPin;
      bool bearConfirm = bearEngulf || bearPin;
      // book, confirmation list: "a small Break of Structure in the trade
      // direction" — without it a sell fires in the middle of a rally just
      // because one candle poked the zone
      double microHi = close[bar - 1], microLo = close[bar - 1];
      for(int k = 1; k <= InpMicroBosLen && bar - k >= 0; k++)
        {
         microHi = MathMax(microHi, close[bar - k]);
         microLo = MathMin(microLo, close[bar - k]);
        }
      bool bosBuyOK  = !InpNeedMicroBos || c > microHi;
      bool bosSellOK = !InpNeedMicroBos || c < microLo;
      bool brokeSupportNow    = false;
      bool brokeResistanceNow = false;

      //--- resolve the open position ---------------------------------------
      if(g_ordOn && bar > g_ordBar)
        {
         if(l <= g_ordEntry && h >= g_ordEntry)
           {
            g_posOn  = true;   g_posBuy = g_ordBuy;  g_posPO2 = g_ordPO2;
            g_posEntry = g_ordEntry; g_posSL = g_ordSL;
            g_posTP1 = g_ordTP1; g_posTP2 = g_ordTP2; g_posTP3 = g_ordTP3;
            // book: Scalper works M5/M15 · Swing works H1/H4/Daily — the
            // timeframe you TRADE on decides it, nothing else
            g_posSwing = (PeriodSeconds(_Period) >= 3600);
            g_posBar = bar;  g_posTime = time[bar];
            g_posZone = g_ordZone; g_posStat = 0;
            g_posBE = false; g_posUid = g_ordUid;
            g_ordOn = false;
           }
         else
           {
            bool blown = g_ordBuy ? (l <= g_ordSL)  : (h >= g_ordSL);
            bool gone  = g_ordBuy ? (h >= g_ordTP1) : (l <= g_ordTP1);
            if(blown || gone || bar - g_ordBar > InpOrderExpiry)
               g_ordOn = false;      // invalidated, ran away, or timed out
           }
        }

      if(g_posOn)
        {
         // On the bar the order filled, only the STOP may be judged. A buy
         // limit fills because price traded DOWN to it, so a low beyond the
         // stop came after the fill and is a real stop-out — but the bar's
         // high may well have printed before the fill, and counting that as a
         // target reached would be reading the future.
         bool entryBar = (bar == g_posBar);
         if(g_posBuy)
           {
            if(l <= g_posSL)              { g_posStat = g_posBE ? -2 : -1; g_posOn = false; }
            else if(entryBar)             { }
            else if(h >= g_posTP3)        { g_posStat =  3; g_posOn = false; }
            else if(h >= g_posTP2 && g_posStat < 2) g_posStat = 2;
            else if(h >= g_posTP1 && g_posStat < 1) g_posStat = 1;
           }
         else
           {
            if(h >= g_posSL)              { g_posStat = g_posBE ? -2 : -1; g_posOn = false; }
            else if(entryBar)             { }
            else if(l <= g_posTP3)        { g_posStat =  3; g_posOn = false; }
            else if(l <= g_posTP2 && g_posStat < 2) g_posStat = 2;
            else if(l <= g_posTP1 && g_posStat < 1) g_posStat = 1;
           }
         // book: once the trade has paid 1:1, make it risk free (Zero Float)
         if(InpBreakEven && g_posOn && g_posStat >= 1 && !g_posBE)
           {
            g_posSL = g_posEntry;
            g_posBE = true;
           }
         if(g_posOn && bar - g_posBar > InpMaxTradeBars)
            g_posOn = false;                 // never block the next setup forever

         // book: a zone whose signal got stopped out has been broken — it is
         // finished, and must not hand out the opposite trade at the same level
         if(InpKillOnStop && !g_posOn && g_posStat == -1 && g_posUid >= 0)
           {
            for(int i = 0; i < ArraySize(g_zones); i++)
               if(g_zones[i].id == g_posUid)
                  g_zones[i].dead = true;
            g_posUid = -1;
           }
        }

      // A Weekly/Daily zone can be years old and hundreds of points away. It is
      // not tradeable any more and it wrecks the chart scale, so drop it.
      for(int i = ArraySize(g_zones) - 1; i >= 0; i--)
        {
         double ref = g_zones[i].htf ? atrA : atr;
         double gap = c > g_zones[i].top ? c - g_zones[i].top
                      : (c < g_zones[i].bot ? g_zones[i].bot - c : 0.0);
         if(gap > ref * InpMaxZoneDistATR)
            RemoveZoneAt(i);
        }

      // book: don't overtrade — manage one setup at a time
      // book p41: at TP1 you take the money off and the stop goes to entry —
      // the setup is FINISHED, it is only riding a free runner. Letting a
      // risk-free trade keep blocking the next signal is what left charts
      // showing a position from 285 bars ago with nothing new behind it.
      bool canFire  = !(InpOneTrade && (g_ordOn || (g_posOn && !g_posBE)));
      bool sigFired = false;

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
                  g_zones[i].fba     = true;
                  g_zones[i].dead    = false;
                 }
               else if(bar - g_zones[i].pendBar >= InpFbaBars)
                 {
                  g_zones[i].wasValid = (g_zones[i].state == 1);
                  g_zones[i].role  = -1;
                  g_zones[i].state = 2;
                  g_zones[i].touches  = 0;
                  g_zones[i].sigTouch = 0;
                  g_zones[i].srr      = false;
                  g_zones[i].fba      = false;
                  g_zones[i].flips++;
                  // a level broken from both sides repeatedly is a range boundary
                  g_zones[i].dead     = (g_zones[i].flips >= InpMaxFlips);
                  g_zones[i].pendBar  = -1;
                  g_zones[i].pendDir  = 0;
                  brokeSupportNow     = true;
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
                               ((g_zones[i].state == 1 && g_zones[i].touches >= needT) ||
                                (g_zones[i].srr && g_zones[i].touches >= 1) ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2)) &&
                               (!InpEntriesHtfOnly || g_zones[i].htf) &&
                               (!InpRequireNested  || ZoneNested(i));
               bool okTrend = !InpTrendFilter || trendUp || trendUnknown ||
                              (InpAllowCounterInv && g_zones[i].state == 2);
               bool okConf  = !InpNeedConfirm || bullConfirm;
               bool fresh   = (g_zones[i].sigTouch != g_zones[i].touches);
               bool rejectOK = InpNeedReject ? (c > g_zones[i].top) : (c > g_zones[i].bot);
               if(tradable && okTrend && okConf && fresh && rejectOK && bosBuyOK && canFire && !sigFired)
                 {
                  g_zones[i].sigTouch = g_zones[i].touches;
                  bool isPO2 = (g_zones[i].state == 2 && g_zones[i].touches == 2);
                  sigFired = true;
                  if(isPO2)
                    {
                     BufPO2Buy[bar] = l - atr * 0.4;
                     Notify("PO2 BUY — Power of Second Touch at " + ZoneText(g_zones[i]), live);
                    }
                  else
                    {
                     BufBuy[bar] = l - atr * 0.3;
                     Notify("BUY — rejection at " + ZoneText(g_zones[i]), live);
                    }
                  // p41/p42: the order sits AT the zone, the stop just
                  // beyond it, and TP1 is the 1:1 line. On the author's own
                  // chart — zone 4706.13-4720, stop 4698.67 — the red 1:1 line
                  // at 4732.33 is exactly the zone plus that 16.8 of risk.
                  double swingLo = MathMin(MathMin(low[bar], low[bar - 1]), low[bar - 2]);
                  double zAtr    = g_zones[i].htf ? atrA : atr;
                  double entry   = c;
                  double rawSl   = MathMin(g_zones[i].bot, swingLo) - zAtr * 0.15;
                  if(InpEntryAtZone)
                    {
                     double mid = (g_zones[i].top + g_zones[i].bot) / 2.0;
                     // the edge price meets first coming back DOWN to a support
                     entry = MathMin(InpEntryEdge ? g_zones[i].top : mid, c);
                     rawSl = MathMin(g_zones[i].bot, swingLo) - zAtr * EffSlBuf();
                    }
                  double risk    = MathMax(MathAbs(entry - rawSl), atr * EffMinSl());
                  double t1, t2, t3;
                  BuildTargets(true, entry, risk, t1, t2, t3);
                  if(InpEntryAtZone)
                     t1 = entry + risk * EffRrTp1();   // p41: TP1 IS the 1:1 line
                  if(InpEntryAtZone)
                    {
                     g_ordOn = true; g_ordBuy = true; g_ordPO2 = isPO2;
                     g_ordEntry = entry; g_ordSL = entry - risk;
                     g_ordTP1 = t1; g_ordTP2 = t2; g_ordTP3 = t3;
                     g_ordBar = bar; g_ordZone = ZoneText(g_zones[i]);
                     g_ordUid = g_zones[i].id;
                    }
                  else
                    {
                     g_posOn = true; g_posBuy = true; g_posPO2 = isPO2;
                     g_posEntry = entry; g_posSL = entry - risk;
                     g_posTP1 = t1; g_posTP2 = t2; g_posTP3 = t3;
                     g_posSwing = (PeriodSeconds(_Period) >= 3600);
                     g_posBar = bar; g_posTime = time[bar];
                     g_posZone = ZoneText(g_zones[i]); g_posStat = 0;
                     g_posBE = false; g_posUid = g_zones[i].id;
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
                  g_zones[i].fba     = true;
                  g_zones[i].dead    = false;
                 }
               else if(bar - g_zones[i].pendBar >= InpFbaBars)
                 {
                  g_zones[i].wasValid = (g_zones[i].state == 1);
                  g_zones[i].role  = 1;
                  g_zones[i].state = 2;
                  g_zones[i].touches  = 0;
                  g_zones[i].sigTouch = 0;
                  g_zones[i].srr      = false;
                  g_zones[i].fba      = false;
                  g_zones[i].flips++;
                  g_zones[i].dead     = (g_zones[i].flips >= InpMaxFlips);
                  g_zones[i].pendBar  = -1;
                  g_zones[i].pendDir  = 0;
                  brokeResistanceNow  = true;
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
                               ((g_zones[i].state == 1 && g_zones[i].touches >= needT) ||
                                (g_zones[i].srr && g_zones[i].touches >= 1) ||
                                (g_zones[i].state == 2 && g_zones[i].touches >= 1 && g_zones[i].touches <= 2)) &&
                               (!InpEntriesHtfOnly || g_zones[i].htf) &&
                               (!InpRequireNested  || ZoneNested(i));
               bool okTrend = !InpTrendFilter || trendDown || trendUnknown ||
                              (InpAllowCounterInv && g_zones[i].state == 2);
               bool okConf  = !InpNeedConfirm || bearConfirm;
               bool fresh   = (g_zones[i].sigTouch != g_zones[i].touches);
               bool rejectOK = InpNeedReject ? (c < g_zones[i].bot) : (c < g_zones[i].top);
               if(tradable && okTrend && okConf && fresh && rejectOK && bosSellOK && canFire && !sigFired)
                 {
                  g_zones[i].sigTouch = g_zones[i].touches;
                  bool isPO2 = (g_zones[i].state == 2 && g_zones[i].touches == 2);
                  sigFired = true;
                  if(isPO2)
                    {
                     BufPO2Sell[bar] = h + atr * 0.4;
                     Notify("PO2 SELL — Power of Second Touch at " + ZoneText(g_zones[i]), live);
                    }
                  else
                    {
                     BufSell[bar] = h + atr * 0.3;
                     Notify("SELL — rejection at " + ZoneText(g_zones[i]), live);
                    }
                  double swingHi = MathMax(MathMax(high[bar], high[bar - 1]), high[bar - 2]);
                  double zAtr    = g_zones[i].htf ? atrA : atr;
                  double entry   = c;
                  double rawSl   = MathMax(g_zones[i].top, swingHi) + zAtr * 0.15;
                  if(InpEntryAtZone)
                    {
                     double mid = (g_zones[i].top + g_zones[i].bot) / 2.0;
                     entry = MathMax(InpEntryEdge ? g_zones[i].bot : mid, c);
                     rawSl = MathMax(g_zones[i].top, swingHi) + zAtr * EffSlBuf();
                    }
                  double risk    = MathMax(MathAbs(rawSl - entry), atr * EffMinSl());
                  double t1, t2, t3;
                  BuildTargets(false, entry, risk, t1, t2, t3);
                  if(InpEntryAtZone)
                     t1 = entry - risk * EffRrTp1();   // p41: TP1 IS the 1:1 line
                  if(InpEntryAtZone)
                    {
                     g_ordOn = true; g_ordBuy = false; g_ordPO2 = isPO2;
                     g_ordEntry = entry; g_ordSL = entry + risk;
                     g_ordTP1 = t1; g_ordTP2 = t2; g_ordTP3 = t3;
                     g_ordBar = bar; g_ordZone = ZoneText(g_zones[i]);
                     g_ordUid = g_zones[i].id;
                    }
                  else
                    {
                     g_posOn = true; g_posBuy = false; g_posPO2 = isPO2;
                     g_posEntry = entry; g_posSL = entry + risk;
                     g_posTP1 = t1; g_posTP2 = t2; g_posTP3 = t3;
                     g_posSwing = (PeriodSeconds(_Period) >= 3600);
                     g_posBar = bar; g_posTime = time[bar];
                     g_posZone = ZoneText(g_zones[i]); g_posStat = 0;
                     g_posBE = false; g_posUid = g_zones[i].id;
                    }
                 }
              }
           }
         g_zones[i].inZonePrev = inZone;
         DrawZone(g_zones[i], g_zones[i].bornTime, time[bar]);
        }

      //--- SRR / RSS qualification (book): Support that broke >=2 Resistances
      //    becomes SRR (buy); Resistance that broke >=2 Supports becomes RSS.
      if(brokeSupportNow || brokeResistanceNow)
        {
         for(int i = 0; i < ArraySize(g_zones); i++)
           {
            if(g_zones[i].state == 2 || g_zones[i].dead)
               continue;
            if(brokeResistanceNow && g_zones[i].role == 1 && g_zones[i].touches == 0 && c > g_zones[i].top)
              {
               g_zones[i].oppBreaks++;
               if(g_zones[i].oppBreaks >= 2)
                  g_zones[i].srr = true;
              }
            if(brokeSupportNow && g_zones[i].role == -1 && g_zones[i].touches == 0 && c < g_zones[i].bot)
              {
               g_zones[i].oppBreaks++;
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
