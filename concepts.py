"""The concept store: everything the app can teach, in one place.

Why a module and not a database table
-------------------------------------
This is content, not user data. It belongs in version control, where a change
is a diff someone can read, a test can check it, and a wrong definition can be
traced to the commit that introduced it. Financial explanations are worse wrong
than absent - a confident wrong account of what free cash flow means will be
believed - so every concept here is written by hand and carries the primary
source it was checked against.

What a concept is
-----------------
An atom of understanding, not an article. Each answers the same five questions
in the same order, so a reader learns the shape once and can then skim any of
them:

    what     one paragraph: what the thing actually is
    why      why it matters, in investing terms
    formula  the arithmetic, where there is any
    read_it  what good and bad look like, with real thresholds
    traps    the specific ways this number misleads people

``see_live`` is what separates this from a glossary. It names where in this app
the concept appears against a real company's real filings, so "earnings per
share" is never only a definition - it is a definition followed by the actual
EPS trail of whatever the reader is looking at.

Adding one
----------
Append to CONCEPTS. test_concepts.py enforces the shape: required fields
non-empty, slugs unique and URL-safe, every ``related`` slug resolving, every
scorecard row covered, every source an https URL. A concept that fails those
does not reach the page.
"""
from __future__ import annotations

# Topics group concepts on the library page and order the reading paths.
TOPICS: dict[str, dict] = {
    "statements": {"name": "Reading the statements",
                   "blurb": "The reports every public company files, and what each one is for."},
    "income":     {"name": "The income statement",
                   "blurb": "What a company sold, what it cost, and what was left."},
    "balance":    {"name": "The balance sheet",
                   "blurb": "What it owns, what it owes, and what is left for shareholders."},
    "cashflow":   {"name": "Cash flow",
                   "blurb": "Profit is an opinion; cash is a fact. Where the money actually went."},
    "returns":    {"name": "Returns and quality",
                   "blurb": "Whether the business earns more than the capital it consumes."},
    "valuation":  {"name": "What it costs",
                   "blurb": "A great business at any price is not a great investment."},
    "events":     {"name": "Filings and events",
                   "blurb": "What companies tell you, and when."},
    "macro":      {"name": "The macro calendar",
                   "blurb": "The releases that move every stock at once, and what they measure."},
    "market":     {"name": "Market structure and risk",
                   "blurb": "Volatility, liquidity, and how prices are actually made."},
}

# Levels order a topic from first principles upward. 1 assumes nothing.
LEVELS: dict[int, str] = {1: "Foundation", 2: "Building on it", 3: "Deeper water"}

CONCEPTS: list[dict] = [
    # ── Reading the statements ───────────────────────────────────────────────
    {
        "slug": "annual-report-10k",
        "name": "The annual report (10-K)",
        "aka": ["10-K", "annual report"],
        "topic": "statements", "level": 1,
        "one_liner": "The audited, once-a-year account of everything a US public company did.",
        "what": "A 10-K is the full annual filing every US public company sends the SEC. It carries the three financial statements, an audit opinion from an outside accounting firm, a plain-English description of the business, and a risk-factor section where the company lists what could go wrong. It is the single most useful document about any company, and it is free.",
        "why": "Everything else — the news story, the analyst note, the number on a stock screener — is downstream of this. When two sources disagree, the 10-K settles it. It is also the only place a company has to describe its own weaknesses in its own words.",
        "formula": "",
        "read_it": "Do not read it front to back. Start with the income statement, balance sheet and cash flow statement. Then read Management's Discussion and Analysis, where the company explains its own numbers. Then the risk factors — skim for anything that changed from last year, because that is where the new worry is.",
        "traps": [
            "The risk factors are written by lawyers to cover everything, so the presence of a risk means little. What matters is a risk that is new this year.",
            "The audit opinion covers whether the numbers follow accounting rules, not whether the business is any good.",
            "Comparative columns are restated when the company changes its accounting or splits its stock, so a figure in this year's 10-K may not match the same year in last year's.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Every figure on the scorecard is pulled from this filing."},
        "related": ["quarterly-report-10q", "current-report-8k", "foreign-annual-report-20f"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "quarterly-report-10q",
        "name": "The quarterly report (10-Q)",
        "aka": ["10-Q", "quarterly filing"],
        "topic": "statements", "level": 1,
        "one_liner": "The lighter update filed for each of the first three quarters.",
        "what": "A 10-Q covers one quarter. It carries the same three statements as a 10-K but in condensed form, and it is reviewed by the auditor rather than fully audited. Companies file three of them a year — the fourth quarter is folded into the 10-K.",
        "why": "It is where a story changes direction. A trend you spotted in the annual report either continues or breaks here, three months at a time, and it is the freshest hard data available between annual reports.",
        "formula": "",
        "read_it": "Compare the quarter to the same quarter a year ago, not to the quarter just gone — most businesses have a seasonal shape, and comparing a holiday quarter to a spring one tells you nothing.",
        "traps": [
            "Reviewed is not audited. The numbers get less scrutiny than the annual ones and are more often revised.",
            "One quarter is noise as often as it is signal. A single soft quarter in an otherwise straight line usually is not the story.",
            "Full-year figures cannot be built by adding four quarters when the company has restated anything mid-year.",
        ],
        "see_live": {"surface": "fundamentals", "note": "The trailing-twelve-month figures on the scorecard are built from four of these."},
        "related": ["annual-report-10k", "earnings-report", "trailing-twelve-months"],
        "sources": ["https://www.sec.gov/answers/form10q.htm"],
    },
    {
        "slug": "current-report-8k",
        "name": "The current report (8-K)",
        "aka": ["8-K", "material event filing"],
        "topic": "statements", "level": 2,
        "one_liner": "The filing a company makes when something happens that cannot wait for the next quarter.",
        "what": "An 8-K reports a specific material event, generally within four business days of it happening. Each one carries a numbered item code saying what kind of event it is: 2.02 is results of operations, 5.02 is a director or officer leaving, 4.02 is the company saying its own past financial statements cannot be relied on.",
        "why": "It is the company telling you something outright rather than you inferring it from the numbers. A 4.02 in particular says every figure you have been reading may be wrong, which is different in kind from a bad ratio.",
        "formula": "",
        "read_it": "Learn four item codes and you have most of the value: 2.02 earnings, 4.02 non-reliance on past financials, 1.03 bankruptcy, 3.01 delisting notice. The last three are serious enough that this app drops a company's verdict a full band when it sees them.",
        "traps": [
            "Most 8-Ks are routine. Volume is not a warning sign; the item code is.",
            "The four-business-day window means a filing dated Monday may describe something that happened the previous Tuesday.",
        ],
        "see_live": {"surface": "fundamentals", "note": "The Filed disclosures panel reads these item codes directly."},
        "related": ["annual-report-10k", "restatement", "earnings-report"],
        "sources": ["https://www.sec.gov/answers/form8k.htm"],
    },
    {
        "slug": "foreign-annual-report-20f",
        "name": "The foreign annual report (20-F)",
        "aka": ["20-F", "foreign private issuer"],
        "topic": "statements", "level": 2,
        "one_liner": "What a non-US company files instead of a 10-K.",
        "what": "A foreign company listed in the US files a 20-F rather than a 10-K. It covers the same ground but usually reports under IFRS rather than US GAAP, and states its accounts in its home currency — TSMC files in New Taiwan dollars, not US dollars.",
        "why": "Some of the best businesses in the world file this form. If you only read 10-Ks you have quietly excluded most of Asia and Europe from your universe.",
        "formula": "",
        "read_it": "Check the currency before you read a single figure. A revenue of 2,894 billion is enormous in dollars and ordinary in New Taiwan dollars. Ratios — margins, returns, multiples — are safe across currencies because both halves cancel; absolute figures are not.",
        "traps": [
            "The US-listed share price is in dollars while the filing is in the home currency. Dividing one by the other produces a number that looks like a valuation and is not.",
            "IFRS and US GAAP name the same line differently, so a screener that only knows US GAAP tags will show blanks rather than the real figures.",
            "Foreign filers report less often — many file half-yearly rather than quarterly.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Search TSM. The card states its figures in TWD and says so."},
        "related": ["annual-report-10k", "reporting-currency"],
        "sources": ["https://www.sec.gov/international/foreign-private-issuers"],
    },
    {
        "slug": "reporting-currency",
        "name": "Reporting currency",
        "aka": ["home currency", "functional currency"],
        "topic": "statements", "level": 2,
        "one_liner": "The currency a company keeps its books in, which is not always the one you trade it in.",
        "what": "A company states its accounts in one currency throughout. For most US filers that is dollars. For a foreign filer it is usually its home currency, sometimes with a convenience translation into dollars for a few lines and a few years — but never for everything.",
        "why": "Mixing two currencies in one calculation produces a plausible-looking number that is wrong by whatever the exchange rate happens to be. A cost of sales in one currency over a revenue in another is a margin off by a factor of thirty and it will not look obviously broken.",
        "formula": "",
        "read_it": "Ratios survive a currency difference because both halves are in the same unit. Absolute figures and any comparison against a share price do not.",
        "traps": [
            "A convenience translation covers a subset of lines and a subset of years. Filling gaps from it silently splices two currencies into one series.",
            "Market data providers often report a foreign company's market cap and 52-week range on the home listing while quoting the price of the US listing.",
        ],
        "see_live": {"surface": "fundamentals", "note": "TSM's card names its currency and refuses to compare it against the dollar price."},
        "related": ["foreign-annual-report-20f", "market-cap"],
        "sources": ["https://www.sec.gov/international/foreign-private-issuers"],
    },
    # ── The income statement ─────────────────────────────────────────────────
    {
        "slug": "revenue",
        "name": "Revenue",
        "aka": ["sales", "top line", "turnover"],
        "topic": "income", "level": 1,
        "one_liner": "What the company sold, before any costs at all.",
        "what": "The total value of goods and services a company delivered to customers over a period. It sits at the top of the income statement, which is why it is called the top line. It is money earned, not money received — a sale made on credit counts here before the cash arrives.",
        "why": "It is the size of the business and the ceiling on everything else. A company can improve margins for a few years by cutting costs, but no amount of cost-cutting grows a shrinking business forever.",
        "formula": "",
        "read_it": "Look at the direction over five years, not the level. Three or more consecutive years of growth is the bar this app scores against. One down year inside a rising trend is usually a cycle; two is a question.",
        "traps": [
            "Revenue growth bought through acquisition is not the same as growth from the existing business, and the income statement does not separate them.",
            "A company can pull revenue forward by loosening credit terms. Rising revenue with receivables rising faster is the classic tell.",
            "Filers renamed this line when the accounting standard changed in 2019, so a naive data pull often shows a gap in the middle of the history.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, first row — with the five-year trail and the consecutive-growth count."},
        "related": ["gross-profit", "net-income", "revenue-growth"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "revenue-growth",
        "name": "Revenue growth",
        "aka": ["sales growth", "top-line growth"],
        "topic": "income", "level": 1,
        "one_liner": "Whether the business is getting bigger, and for how long it has been.",
        "what": "The change in revenue from one year to the next, usually as a percentage. What matters more than any single year's rate is the streak: how many consecutive years the line has gone up.",
        "why": "A streak is evidence of something durable — demand that keeps showing up. A single good year can be one large contract, a weak comparison, or an acquisition.",
        "formula": "(This year's revenue ÷ last year's revenue) − 1",
        "read_it": "This app scores three or more consecutive growth years as a pass, two as a partial. A company with a dip in the middle of five otherwise rising years scores as one consecutive year, which is deliberately harsh — the dip is the information.",
        "traps": [
            "Percentage growth off a small base flatters. Twenty per cent on 50 million is not twenty per cent on 50 billion.",
            "Inflation alone lifts revenue. In a high-inflation year, low single-digit growth may be a real-terms decline.",
            "Currency movement swings a foreign filer's reported growth without anything changing in the business.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2 shows the trail and counts the streak for you."},
        "related": ["revenue", "gross-margin"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "gross-profit",
        "name": "Gross profit",
        "aka": ["gross income"],
        "topic": "income", "level": 1,
        "one_liner": "What is left after the direct cost of making the thing.",
        "what": "Revenue minus the cost of revenue — the materials, manufacturing and direct labour that went into what was sold. It excludes everything indirect: research, sales teams, head office, interest, tax.",
        "why": "It is the rawest measure of whether the product itself makes money. A business that cannot cover its direct costs has a broken product, not a cost-control problem.",
        "formula": "Revenue − cost of revenue",
        "read_it": "Read it as a margin rather than a dollar figure, so it can be compared across years and companies.",
        "traps": [
            "Not every company tags this line. Some run revenue straight into total costs and expenses, which leaves the field blank on data feeds even though the arithmetic is available.",
            "What counts as a direct cost varies by company, so gross margin is comparable within an industry and misleading across industries.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Rebuilt from revenue and cost of revenue when the filer does not tag it."},
        "related": ["gross-margin", "revenue", "operating-income"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "gross-margin",
        "name": "Gross margin",
        "aka": ["gross profit margin"],
        "topic": "income", "level": 1,
        "one_liner": "The share of every sale left after making the product — the clearest read on pricing power.",
        "what": "Gross profit as a percentage of revenue. If gross margin is 60%, then sixty cents of every dollar of sales is left over before the company pays for research, sales, admin, interest or tax.",
        "why": "This is where pricing power shows up. A company that can raise prices without losing customers holds its gross margin through cost inflation. A company competing on price alone watches it erode year after year, and no amount of cost discipline elsewhere fixes that.",
        "formula": "Gross profit ÷ revenue × 100",
        "read_it": "The direction matters more than the level. Software sits at 70–90% and a grocer at 25% and neither fact tells you anything on its own. Stable or rising over five years is the pass; a steady slide is the warning, even from a high starting point.",
        "traps": [
            "Comparing across industries is meaningless. Compare a company to its own history and to direct competitors.",
            "A one-year jump often comes from a change in what the company counts as cost of revenue, not from pricing.",
            "The newest figure on this app's card is a trailing-twelve-month number from a data feed, not the fiscal year beside it — it is marked TTM for that reason.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, with the five-year trail and the margin chart below."},
        "related": ["gross-profit", "operating-margin", "net-margin", "moat"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "operating-income",
        "name": "Operating income",
        "aka": ["operating profit", "EBIT"],
        "topic": "income", "level": 1,
        "one_liner": "Profit from actually running the business, before financing and tax.",
        "what": "What is left after both the direct cost of the product and the cost of running the company — research, sales and marketing, administration. It stops short of interest and tax, so it measures the business rather than how it is financed or where it is domiciled.",
        "why": "It is the cleanest comparison between two companies in the same industry, because it strips out debt loads and tax rates that have nothing to do with operations.",
        "formula": "Revenue − cost of revenue − operating expenses",
        "read_it": "Read as a margin. Rising operating margin with flat gross margin means the company is getting more efficient. Rising gross margin with flat operating margin means it is spending the gains.",
        "traps": [
            "Restructuring charges and one-off write-downs land here and can make a normal year look terrible.",
            "Companies that capitalise rather than expense their development costs report higher operating income for the same underlying activity.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, rebuilt from the tagged expense lines when the filer reports no subtotal."},
        "related": ["operating-margin", "gross-profit", "net-income", "roic"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "operating-margin",
        "name": "Operating margin",
        "aka": ["operating profit margin", "EBIT margin"],
        "topic": "income", "level": 1,
        "one_liner": "The share of every sale left after running the whole business.",
        "what": "Operating income as a percentage of revenue. Where gross margin asks whether the product makes money, operating margin asks whether the company does — it carries the cost of the research, the sales force and the head office that gross margin ignores.",
        "why": "It captures both pricing power and cost discipline in one number. A company with a great product and a bloated cost base has a high gross margin and a poor operating margin, and this is where that shows.",
        "formula": "Operating income ÷ revenue × 100",
        "read_it": "Stable or rising over five years is the pass. Watch it against gross margin: if gross margin holds while operating margin falls, the problem is overhead, not the product.",
        "traps": [
            "Operating leverage means margin naturally expands as revenue grows, because fixed costs spread wider. Expanding margin during a growth year is less impressive than the same expansion in a flat year.",
            "Serial one-off charges are not one-off. A company that takes restructuring charges every year is really carrying them as an operating cost.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, plotted in the margin trend chart."},
        "related": ["operating-income", "gross-margin", "net-margin"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "net-income",
        "name": "Net income",
        "aka": ["net profit", "the bottom line", "profit after tax"],
        "topic": "income", "level": 1,
        "one_liner": "What is left for shareholders after absolutely everything.",
        "what": "The final line of the income statement: revenue minus every cost, including interest on debt and tax. It is what accounting says the owners earned over the period.",
        "why": "It is the number headlines quote and the numerator of earnings per share and the P/E ratio. It is also the number most exposed to accounting judgement, which is why the cash flow statement exists as a check on it.",
        "formula": "Revenue − all costs, interest and tax",
        "read_it": "Always read it beside free cash flow. Net income above cash generation year after year is the single most useful warning sign in fundamental analysis.",
        "traps": [
            "Non-cash charges such as goodwill impairment can turn a profitable year into a reported loss without a dollar leaving the building.",
            "One-off gains — selling a division, a legal settlement, a tax benefit — inflate a single year and do not repeat.",
            "For a company with minority interests, the figure attributable to the parent's shareholders differs from the total.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, and plotted against free cash flow in the cash quality chart."},
        "related": ["earnings-quality", "eps", "net-margin", "free-cash-flow"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "net-margin",
        "name": "Net margin",
        "aka": ["net profit margin"],
        "topic": "income", "level": 1,
        "one_liner": "Cents of profit per dollar of sales, after everything.",
        "what": "Net income as a percentage of revenue. It is the last of the three margins and the only one that has absorbed everything: the product cost, the running cost, the interest on borrowings and the tax bill.",
        "formula": "Net income ÷ revenue × 100",
        "why": "It is the whole income statement compressed into one number, which makes it a good summary and a poor diagnostic — it tells you the outcome without telling you which part of the business produced it.",
        "read_it": "Positive and trending up is the pass. When it moves, walk back up the statement: gross margin first, then operating margin, to find where the change happened.",
        "traps": [
            "A falling tax rate lifts net margin without the business improving at all.",
            "Highly indebted companies show thin net margins on healthy operating margins, because interest eats the difference.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, and the third line on the margin chart."},
        "related": ["net-income", "operating-margin", "gross-margin"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "eps",
        "name": "Earnings per share (EPS)",
        "aka": ["EPS", "earnings per share", "diluted EPS"],
        "topic": "income", "level": 1,
        "one_liner": "Net income divided among the shares — the profit attached to one share you own.",
        "what": "Net income divided by the number of shares outstanding. Basic EPS uses the weighted average shares actually issued. Diluted EPS also counts shares that would exist if every option, warrant and convertible bond were exercised, so it is the more conservative and the one worth reading.",
        "why": "It is the bridge between the company and your position. Net income growing while the share count grows faster means your slice shrank even though the company got bigger — and only EPS shows that.",
        "formula": "Diluted EPS = net income available to common shareholders ÷ weighted average diluted shares",
        "read_it": "Read diluted, never basic, and read it across five years. Then check the share count separately: EPS rising because net income rose is a business improving; EPS rising because the company bought back stock is financial engineering, which is not worthless but is a different thing.",
        "traps": [
            "Buybacks lift EPS without any operating improvement. Always look at EPS growth and share-count change together.",
            "A stock split changes EPS mechanically. Filed per-share figures are as-filed and prior years are not always restated in older filings, so splicing two reports can show a fake collapse.",
            "'Adjusted' or 'non-GAAP' EPS is the company's own preferred version with costs it considers unrepresentative removed. It is often reasonable and it is never audited.",
            "For a foreign filer, EPS is in the home currency while the share price is in dollars. Dividing one by the other is not a P/E.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2 shows the five-year diluted EPS trail as filed, and the split guard flags a spliced series."},
        "related": ["net-income", "share-dilution", "pe-ratio", "earnings-report"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "eps-growth",
        "name": "EPS growth",
        "aka": ["earnings growth"],
        "topic": "income", "level": 1,
        "one_liner": "Whether the profit attached to your share is getting bigger.",
        "what": "The change in diluted earnings per share from one period to the next. Because the denominator can move as well as the numerator, it captures two separate stories at once: whether the company earned more, and whether it divided those earnings among more or fewer shares.",
        "why": "Over long horizons share prices track earnings per share more closely than they track revenue or net income. It is the compounding engine.",
        "formula": "(This period's diluted EPS ÷ the prior period's) − 1",
        "read_it": "Against the five-year trail, not one year. Then ask what drove it — more profit, or fewer shares.",
        "traps": [
            "Growth off a loss year or a near-zero base produces meaningless percentages.",
            "A single large buyback can carry EPS growth for a year or two and then stop.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 2, scored against the filed trail with a trailing-twelve-month overlay."},
        "related": ["eps", "share-dilution", "net-income"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "share-dilution",
        "name": "Share dilution",
        "aka": ["dilution", "share count growth", "diluted shares outstanding"],
        "topic": "income", "level": 2,
        "one_liner": "The company issuing new shares, which quietly shrinks the slice you own.",
        "what": "An increase in the number of shares outstanding, usually from stock issued to employees or to fund an acquisition. Every new share divides the same profit among more owners.",
        "why": "It is the cost that never appears as a cost. A company can grow revenue and net income every year and still leave shareholders worse off per share, and nothing on the income statement flags it.",
        "formula": "(This year's diluted shares ÷ last year's) − 1",
        "read_it": "Under about 2% a year is normal for a company paying staff partly in stock. Consistently above that means you are funding the payroll out of your own ownership. Shrinking share count means buybacks.",
        "traps": [
            "A stock split multiplies the share count without diluting anyone. A ten-for-one split looks like 900% dilution to any tool reading two filings on different bases — this app pulls the series from one filing to avoid exactly that.",
            "Share-based compensation is a real cost recognised on the income statement, but the dilution it causes shows up separately in the share count.",
            "Buybacks that exactly offset issuance leave the count flat while the company spends real cash to stand still.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 4, with a split guard that refuses to report a split as dilution."},
        "related": ["eps", "stock-split", "net-income"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "stock-split",
        "name": "Stock split",
        "aka": ["split", "forward split", "reverse split"],
        "topic": "income", "level": 2,
        "one_liner": "Cutting each share into more pieces — the pie is unchanged, the slices are smaller.",
        "what": "A company multiplies its share count by some factor and divides the price by the same factor. A ten-for-one split turns one $1,000 share into ten $100 shares. Nothing about the business changes. A reverse split does the opposite and is often a company trying to stay above an exchange's minimum price.",
        "why": "Splits wreck naive data analysis. Price history is nearly always restated for splits; per-share figures inside old filings are not. Mixing the two silently compares two different scales.",
        "formula": "",
        "read_it": "If a share count jumps by a clean multiple in one year with no matching change in revenue or net income, suspect a split before you suspect dilution.",
        "traps": [
            "A filer restates prior years post-split inside its new report, but its older reports keep the pre-split figures. Building a series from whichever report first mentioned each year splices two bases together.",
            "A reverse split is usually a signal about the share price rather than the business, and it is worth asking why it was needed.",
        ],
        "see_live": {"surface": "fundamentals", "note": "The split notice at the top of the card appears when a break in basis is detected."},
        "related": ["share-dilution", "eps"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    # ── The balance sheet ────────────────────────────────────────────────────
    {
        "slug": "balance-sheet",
        "name": "The balance sheet",
        "aka": ["statement of financial position"],
        "topic": "balance", "level": 1,
        "one_liner": "What the company owns, what it owes, and what is left over — on one day.",
        "what": "A snapshot at a single date. Assets are what the company owns; liabilities are what it owes; equity is the difference. It balances by construction: everything owned was funded either by borrowing or by owners, so assets always equal liabilities plus equity.",
        "why": "The income statement tells you how a year went. The balance sheet tells you whether the company can survive a bad one.",
        "formula": "Assets = liabilities + equity",
        "read_it": "It is a photograph, not a film. A company can pay down debt the week before its year end and reload it after. Read several years side by side.",
        "traps": [
            "Assets are carried at historical cost less depreciation, not what they would fetch today. Property bought in 1990 sits at 1990 prices.",
            "The most valuable things a company owns — a brand, a research team, a customer base built over decades — appear nowhere, because they were never bought.",
            "Balance sheet dates for a foreign filer or a company with an odd fiscal year will not line up with a calendar quarter.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1 scores five things off this statement."},
        "related": ["current-ratio", "debt-to-equity", "equity", "goodwill"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "equity",
        "name": "Shareholders' equity",
        "aka": ["book value", "net assets", "stockholders equity"],
        "topic": "balance", "level": 1,
        "one_liner": "What would be left for shareholders if every asset were sold at its carrying value and every debt repaid.",
        "what": "Total assets minus total liabilities. It is built from money originally paid in by shareholders plus every year of profit the company kept rather than paid out.",
        "why": "It is the denominator of return on equity and the cushion that absorbs losses before lenders are at risk.",
        "formula": "Total assets − total liabilities",
        "read_it": "Growing equity with no new share issuance means the company is retaining profit. Falling equity in a profitable company usually means large buybacks or dividends.",
        "traps": [
            "Aggressive buybacks can drive equity negative while the business is perfectly healthy. Return on equity then becomes meaningless or absurd.",
            "Equity is a book figure, not a market one. It has no direct relationship to market capitalisation.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Used in debt-to-equity in Section 1 and return on equity in Section 4."},
        "related": ["balance-sheet", "roe", "retained-earnings", "debt-to-equity"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "current-ratio",
        "name": "Current ratio",
        "aka": ["working capital ratio"],
        "topic": "balance", "level": 1,
        "one_liner": "Whether the company can pay next year's bills with next year's assets.",
        "what": "Current assets — cash, receivables, inventory, anything expected to turn into cash within a year — divided by current liabilities, the obligations due in the same window.",
        "why": "It is the first read on whether a company is in near-term trouble. Businesses do not usually fail because they are unprofitable; they fail because they run out of cash while still profitable on paper.",
        "formula": "Current assets ÷ current liabilities",
        "read_it": "Above 1.5 is comfortable and is the bar this app scores against. Below 1.0 means short-term obligations exceed short-term assets, which is a genuine stress signal. Very high — above 3 or 4 — can mean idle cash earning nothing.",
        "traps": [
            "Inventory counts as a current asset and may not sell at carrying value, or at all. A retailer with a warehouse of last season's stock has a flattering current ratio.",
            "Some healthy business models run below 1 by design, because they collect from customers before paying suppliers.",
            "This app shows the provider's latest-quarter figure as the headline and the annual balance-sheet division as the working, and says so when they differ.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, first row, with both sides of the division shown."},
        "related": ["balance-sheet", "cash-coverage", "short-term-debt"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "debt-to-equity",
        "name": "Debt to equity",
        "aka": ["D/E", "leverage ratio", "gearing"],
        "topic": "balance", "level": 1,
        "one_liner": "How much of the business is funded by lenders rather than owners.",
        "what": "Total debt divided by shareholders' equity — the mix between money the company borrowed and money its owners put in or left in. A ratio of 1.0 means it owes as much as the owners have in it; 0.1 means it is funded almost entirely by its owners.",
        "why": "Debt magnifies both directions. It lifts returns in good years and it is what turns a bad year into an existential one, because interest is owed whether or not the company earned anything.",
        "formula": "Total debt ÷ shareholders' equity",
        "read_it": "Below 1.0 is the bar here. What counts as high varies enormously by industry — utilities and banks run leveraged by design, software companies rarely borrow at all — so compare within an industry.",
        "traps": [
            "Definitions differ on whether to include leases, pensions and short-term borrowings. Two sources can give genuinely different numbers for the same company.",
            "Negative equity from buybacks makes the ratio meaningless.",
            "A low ratio is not automatically good. A company that could borrow cheaply to fund high-return projects and does not is leaving value unclaimed.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, with the annual division shown and the quarterly headline labelled."},
        "related": ["equity", "total-debt", "cash-coverage"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "total-debt",
        "name": "Total debt",
        "aka": ["borrowings", "interest-bearing debt"],
        "topic": "balance", "level": 1,
        "one_liner": "Money the company borrowed and has to give back.",
        "what": "Long-term borrowings plus the portion due within a year, plus finance leases. It excludes operating liabilities like money owed to suppliers, which are not borrowed money.",
        "why": "Debt is the claim that ranks ahead of yours. Lenders are paid before shareholders, in good times and in liquidation.",
        "formula": "Long-term debt + current portion of long-term debt + short-term borrowings",
        "read_it": "Read it against equity, against cash, and against revenue growth. Debt rising faster than revenue means leverage is increasing relative to the business — this app treats that as an automatic downgrade.",
        "traps": [
            "Debt on its own says nothing. A company with 10 billion of debt and 30 billion of cash is not leveraged.",
            "Off-balance-sheet arrangements and unconsolidated joint ventures can carry obligations that never appear in this figure.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, and one of the automatic downgrade triggers."},
        "related": ["debt-to-equity", "short-term-debt", "cash-coverage"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "short-term-debt",
        "name": "Short-term debt",
        "aka": ["current portion of debt", "debt due within a year", "current maturities"],
        "topic": "balance", "level": 2,
        "one_liner": "The slice of borrowings that has to be repaid or refinanced inside twelve months.",
        "what": "Everything the company must repay lenders within a year: commercial paper, drawn credit lines, and the current portion of long-term borrowings.",
        "why": "This is the number that actually threatens a company in a credit squeeze. Debt maturing in 2035 is not a near-term problem; debt maturing in March is.",
        "formula": "",
        "read_it": "Compare it against cash and short-term investments. A company holding several times its near-term maturities in liquid assets is not under pressure regardless of how much total debt it carries.",
        "traps": [
            "Not every filer tags this separately, and a comparison against total debt instead is far stricter — it makes any company with termed-out long-term debt look exposed when it is not.",
            "A figure from an old balance sheet is not evidence about today. This app refuses to score it if the most recent available disclosure is more than a year stale.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, cash coverage row — it says which basis it used."},
        "related": ["total-debt", "cash-coverage", "current-ratio"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "cash-coverage",
        "name": "Cash coverage of debt",
        "aka": ["liquidity coverage", "cash covers debt"],
        "topic": "balance", "level": 2,
        "one_liner": "Whether liquid assets cover what is actually due soon.",
        "what": "Cash and short-term investments measured against the debt maturing within a year. It deliberately ignores debt due further out, because a bond maturing in a decade is not what forces a company into a bad decision this quarter.",
        "why": "It answers the survival question directly. A company that can repay everything due this year out of the bank does not need markets to stay open or lenders to stay friendly.",
        "formula": "(Cash + short-term investments) ÷ debt due within a year",
        "read_it": "Above 1 means covered. Well above 1 with a fortress balance sheet is common in large technology companies and is one reason they survive downturns that kill leveraged competitors.",
        "traps": [
            "Cash held overseas may carry a tax cost to repatriate, so not all of it is freely available.",
            "Short-term investments are not always as liquid as the label suggests.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, showing both sides and the balance-sheet date it read them at."},
        "related": ["short-term-debt", "current-ratio", "total-debt"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "goodwill",
        "name": "Goodwill and intangibles",
        "aka": ["goodwill", "intangible assets"],
        "topic": "balance", "level": 2,
        "one_liner": "The premium paid for acquisitions, sitting on the balance sheet as an asset.",
        "what": "When a company buys another for more than the fair value of its identifiable assets, the excess is recorded as goodwill. Intangibles are identifiable non-physical assets — patents, customer relationships, brands acquired in a deal.",
        "why": "Goodwill is the only large asset that can vanish overnight. If an acquisition disappoints, the company writes it down, and a write-down turns a profitable year into a reported loss without a dollar moving.",
        "formula": "(Goodwill + intangibles) ÷ total assets",
        "read_it": "Under 30% of total assets is the bar here. Above that, a meaningful share of what the company appears to own is a judgement about past deals rather than something you could sell.",
        "traps": [
            "Goodwill is not amortised under US GAAP; it sits at cost until it is tested and written down, which is why the fall is sudden rather than gradual.",
            "A company that has never acquired anything has no goodwill, which is not a virtue — it is a description of its strategy.",
            "A year-on-year fall in goodwill usually means an impairment but can also be a divestiture or a currency movement.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, and two of the automatic downgrade triggers watch it."},
        "related": ["balance-sheet", "net-income", "impairment"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "impairment",
        "name": "Impairment",
        "aka": ["write-down", "goodwill impairment"],
        "topic": "balance", "level": 2,
        "one_liner": "The company admitting an asset is worth less than the books say.",
        "what": "A charge taken when the carrying value of an asset exceeds what it can realistically recover. It reduces the asset and runs through the income statement as an expense.",
        "why": "An impairment is management stating in public that a past decision did not work. It is non-cash, so it does not threaten solvency, but it is a direct admission about capital allocation.",
        "formula": "",
        "read_it": "Treat it as information about judgement rather than about this year's earnings. One impairment is a bad deal; a pattern is a habit.",
        "traps": [
            "Because it is non-cash, companies encourage you to look past it. The cash was spent when the acquisition was made; this is the acknowledgement.",
            "Impairments cluster in bad years, when management can bury them in an already-poor result.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Detected as a year-on-year fall in goodwill; it drops the verdict a full band."},
        "related": ["goodwill", "net-income", "earnings-quality"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "retained-earnings",
        "name": "Retained earnings",
        "aka": ["accumulated earnings", "retained earnings growth"],
        "topic": "balance", "level": 2,
        "one_liner": "Every dollar of profit the company ever earned and did not pay out.",
        "what": "A running total, from the day the company started, of cumulative profit minus cumulative dividends. A company that has lost money for most of its life carries a negative figure, called an accumulated deficit.",
        "why": "It is the long-memory measure. One good year is invisible here; a decade of them is unmistakable.",
        "formula": "Prior retained earnings + net income − dividends",
        "read_it": "Growing over three to five years is the pass. Falling while profitable means dividends and buybacks exceed earnings, which is sustainable for a while and not forever.",
        "traps": [
            "A young, fast-growing company may show an accumulated deficit while being an excellent business.",
            "Buybacks are usually charged against equity rather than retained earnings, so the line can keep rising while total equity falls.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 1, with the five-year trail."},
        "related": ["equity", "net-income"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    # ── Cash flow ────────────────────────────────────────────────────────────
    {
        "slug": "cash-flow-statement",
        "name": "The cash flow statement",
        "aka": ["statement of cash flows"],
        "topic": "cashflow", "level": 1,
        "one_liner": "Where the money actually went — the check on everything the income statement claims.",
        "what": "A reconciliation of cash in and cash out, split three ways: operating (running the business), investing (buying and selling assets), and financing (borrowing, repaying, issuing and buying back shares, paying dividends).",
        "why": "Profit involves judgement about when a sale counts and how fast an asset wears out. Cash does not. When the two disagree over several years, the cash is usually telling the truth.",
        "formula": "",
        "read_it": "Read operating cash flow against net income first. Then look at investing to see what the company is buying, and financing to see whether it is returning money to owners or raising it from them.",
        "traps": [
            "A single year's cash flow swings on working capital timing. Three to five years is the shortest honest window.",
            "Operating cash flow can be flattered by stretching payments to suppliers, which is borrowing from your vendors by another name.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 3 scores five things off this statement."},
        "related": ["operating-cash-flow", "free-cash-flow", "capex", "earnings-quality"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "operating-cash-flow",
        "name": "Operating cash flow",
        "aka": ["OCF", "cash from operations"],
        "topic": "cashflow", "level": 1,
        "one_liner": "Cash the core business actually generated, before investment.",
        "what": "Net income adjusted back to cash: non-cash charges like depreciation added in, and changes in working capital taken out. It is what the business produced in real money before deciding what to spend it on.",
        "why": "It is the fuel. Everything a company can do without borrowing or issuing stock — invest, acquire, pay dividends, buy back shares — comes out of this line.",
        "formula": "Net income + non-cash charges ± changes in working capital",
        "read_it": "Rising over three to five years is the pass. Compare its shape to net income's: they should broadly track.",
        "traps": [
            "Depreciation added back is not free money. The assets it represents will need replacing, which is what capital expenditure is for.",
            "A one-off working capital release — collecting receivables hard, running inventory down — lifts one year and reverses in the next.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 3, with the five-year trail."},
        "related": ["free-cash-flow", "capex", "earnings-quality", "cash-flow-statement"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "capex",
        "name": "Capital expenditure",
        "aka": ["capex", "purchases of property, plant and equipment"],
        "topic": "cashflow", "level": 1,
        "one_liner": "Cash spent on the physical things the business runs on.",
        "what": "Money spent buying or upgrading long-lived assets — factories, equipment, servers. It appears in the investing section of the cash flow statement, not as an expense on the income statement, because the asset is expected to last for years.",
        "why": "It separates businesses that print cash from businesses that consume it. Two companies with identical operating cash flow are entirely different investments if one must reinvest most of it just to stand still.",
        "formula": "Capex ÷ revenue × 100",
        "read_it": "Under 10% of revenue is the bar here, which suits asset-light businesses. Chip fabricators, telecoms and utilities run far above that by nature, so read it against the industry and against the company's own history — a sudden jump is either a growth bet or a maintenance backlog coming due.",
        "traps": [
            "Maintenance capex (keeping what you have) and growth capex (building more) are different in meaning and are rarely disclosed separately.",
            "Cutting capex lifts free cash flow immediately and damages the business slowly, which makes it a favourite lever before a sale or a bonus year.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 3, as a percentage of revenue."},
        "related": ["free-cash-flow", "operating-cash-flow", "roic"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "free-cash-flow",
        "name": "Free cash flow",
        "aka": ["FCF"],
        "topic": "cashflow", "level": 1,
        "one_liner": "What is left after running the business and keeping it standing — the money genuinely available to owners.",
        "what": "Operating cash flow minus capital expenditure. It is the cash a company could hand to shareholders without shrinking itself.",
        "why": "This is the number a business is ultimately worth the discounted value of. Earnings can be shaped by accounting choices; free cash flow is what is left in the bank.",
        "formula": "Operating cash flow − capital expenditure",
        "read_it": "Positive is the bar, and consistently positive across a cycle is what actually matters. A young company burning cash to build something is not automatically bad, but you should be able to say when it is expected to turn.",
        "traps": [
            "Definitions vary — some exclude acquisitions, some include leases. Compare like with like.",
            "One strong year can come from delaying capex. Look at the capex line beside it.",
            "This app's trailing-twelve-month free cash flow comes from a dollar-denominated feed, so it is not used for a company that reports in another currency.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 3, and charted against net income."},
        "related": ["operating-cash-flow", "capex", "earnings-quality", "price-to-fcf"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "earnings-quality",
        "name": "Earnings quality",
        "aka": ["FCF vs net income", "cash conversion"],
        "topic": "cashflow", "level": 2,
        "one_liner": "Whether reported profit is backed by actual cash.",
        "what": "A comparison of free cash flow against net income. If a company reports a billion of profit and generates a billion of free cash, the profit is real in the most literal sense.",
        "why": "This is the most useful single check in fundamental analysis. Almost every accounting scandal of the last forty years shows up here first as profit that persistently exceeds cash, years before anything else breaks.",
        "formula": "Free cash flow ÷ net income",
        "read_it": "At or above 1.0 is a clean pass. Around 0.75 is a company absorbing working capital — worth flagging and not damning, which is why this app gives partial credit there. Persistently below that deserves an explanation you can actually find.",
        "traps": [
            "Fast-growing companies legitimately run below 1 because growth consumes working capital. Growth explains a gap; it does not explain a widening one.",
            "The ratio is meaningless when net income is negative or near zero.",
            "One year proves nothing. It is the multi-year pattern that carries the signal.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 3, and the cash quality chart plots both series."},
        "related": ["free-cash-flow", "net-income", "operating-cash-flow"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "financing-cash-flow",
        "name": "Financing cash flow",
        "aka": ["cash from financing", "debt financing"],
        "topic": "cashflow", "level": 2,
        "one_liner": "Whether the company is returning money to its funders or raising more from them.",
        "what": "Cash raised from issuing debt or shares, minus cash used repaying debt, buying back shares and paying dividends. It is the third section of the cash flow statement, and unlike the other two it describes the company's relationship with its funders rather than with its customers.",
        "why": "The sign tells you which direction the company is running. Persistently negative is a mature business returning capital. Persistently positive means it is funding itself from outside, which is fine while markets are open and dangerous when they close.",
        "formula": "",
        "read_it": "Compare it to operating cash flow. Financing inflows above roughly half of operating cash flow means the business is meaningfully dependent on outside money.",
        "traps": [
            "A single large positive year may be one refinancing or one acquisition, not a pattern.",
            "Negative financing cash flow is not automatically good — a company borrowing cheaply to build something valuable is a reasonable use of the balance sheet.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 3, shown against operating cash flow."},
        "related": ["operating-cash-flow", "total-debt", "share-dilution"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    # ── Returns and quality ──────────────────────────────────────────────────
    {
        "slug": "roe",
        "name": "Return on equity (ROE)",
        "aka": ["ROE"],
        "topic": "returns", "level": 1,
        "one_liner": "Profit earned on each dollar of shareholders' money.",
        "what": "Net income divided by shareholders' equity. If equity is 10 billion and the company earns 2 billion, return on equity is 20%.",
        "why": "It answers the owner's question directly: given what has been put in and left in, what is it earning? Sustained high returns compound; that is the whole mechanism of long-term equity returns.",
        "formula": "Net income ÷ shareholders' equity × 100",
        "read_it": "Above 15% is the bar here. Sustained above 20% for a decade is rare and usually means something is protecting the business.",
        "traps": [
            "Debt inflates it. Borrowing shrinks equity and lifts the ratio without the business improving — which is why return on invested capital is the better measure.",
            "Buybacks shrink equity and lift ROE mechanically. Taken far enough, equity goes negative and the ratio becomes nonsense.",
            "A trailing-twelve-month ROE from a data provider uses average equity and trailing income, so it will not equal a year-end division done by hand.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 4, with the division shown."},
        "related": ["roic", "equity", "net-income", "debt-to-equity"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "roic",
        "name": "Return on invested capital (ROIC)",
        "aka": ["ROIC", "return on capital"],
        "topic": "returns", "level": 2,
        "one_liner": "Profit earned on all the money in the business, borrowed and owned alike.",
        "what": "After-tax operating profit divided by the total capital funding the business — equity plus debt. Unlike return on equity, it cannot be flattered by borrowing, because borrowed money sits in the denominator.",
        "why": "It is the closest single number to 'is this a good business'. A company earning 20% on all its capital while its capital costs 8% creates value with every dollar it reinvests. One earning 5% destroys value by growing.",
        "formula": "Operating income × (1 − tax rate) ÷ (equity + total debt) × 100",
        "read_it": "Above 10% is the bar. What matters more is persistence: one good year is a cycle, a decade of them is the arithmetic signature of a moat.",
        "traps": [
            "Definitions vary widely — whether to include cash, leases, goodwill. Compare a company to its own history using one definition rather than across sources.",
            "Heavy acquirers carry large goodwill in the denominator, which depresses the ratio and is arguably correct: they paid that money.",
            "A high figure in a single cyclical peak year says nothing about the trough.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 4, with a year-by-year chart underneath."},
        "related": ["roe", "moat", "operating-income", "capex"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "moat",
        "name": "Economic moat",
        "aka": ["moat", "competitive advantage", "durable advantage"],
        "topic": "returns", "level": 2,
        "one_liner": "Whatever stops competitors from copying a profitable business and taking the profit away.",
        "what": "A structural advantage that lets a company keep earning above-average returns for a long time: a brand people pay more for, switching costs that make leaving painful, a network that gets better as it grows, or a scale advantage rivals cannot match.",
        "why": "Competition normally drags returns down toward the cost of capital. A company that stays well above it for many years is being protected by something, and identifying what is the most valuable work in equity research.",
        "formula": "",
        "read_it": "The numbers can tell you a moat is probably there — returns on capital held above 10% year after year while gross margin does not erode. They cannot tell you what it is. That still requires reading the annual report and understanding who the customers are and why they stay.",
        "traps": [
            "A high margin today is not a moat. The question is always whether it survives a competitor trying hard to take it.",
            "Moats erode. Distribution advantages died with the internet; scale advantages die when technology resets the cost curve.",
            "This app scores the arithmetic signature only, and says so. It is a prompt to investigate, not a conclusion.",
        ],
        "see_live": {"surface": "fundamentals", "note": "Section 4, scored from sustained ROIC and non-eroding gross margin."},
        "related": ["roic", "gross-margin", "revenue-growth"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    # ── What it costs ────────────────────────────────────────────────────────
    {
        "slug": "market-cap",
        "name": "Market capitalisation",
        "aka": ["market cap"],
        "topic": "valuation", "level": 1,
        "one_liner": "What the market says the whole company is worth right now.",
        "what": "Share price multiplied by shares outstanding — what it would cost to buy every share at today's price. It is the market's running verdict on the whole company, updated every second the exchange is open, and it has no necessary relationship to any figure in the accounts.",
        "why": "It is the denominator of common sense. A number is only large or small relative to what the company is worth — 5 billion of debt is nothing at a 500 billion company and existential at a 6 billion one.",
        "formula": "Share price × shares outstanding",
        "read_it": "Use it to size everything else. It is also how the market is usually segmented: large cap, mid cap, small cap — categories that mostly describe liquidity and volatility rather than quality.",
        "traps": [
            "It is not what it would cost to buy the company. That is enterprise value, which adds debt and subtracts cash.",
            "For a foreign company with a US listing, data providers often report the market cap of the home listing in the home currency while quoting the US share price in dollars.",
        ],
        "see_live": {"surface": "fundamentals", "note": "In the What it costs strip, labelled with the listing's currency."},
        "related": ["pe-ratio", "reporting-currency", "share-dilution"],
        "sources": ["https://www.investor.gov/introduction-investing/investing-basics/glossary/market-capitalization"],
    },
    {
        "slug": "pe-ratio",
        "name": "Price to earnings (P/E)",
        "aka": ["P/E", "PE ratio", "earnings multiple"],
        "topic": "valuation", "level": 1,
        "one_liner": "How many dollars of price you pay for each dollar of annual profit.",
        "what": "Share price divided by earnings per share. A P/E of 25 means you are paying 25 dollars for each dollar the company earned in the last twelve months. Trailing P/E uses profit already reported; forward P/E uses an analyst estimate of profit not yet earned.",
        "why": "It is the shorthand for how much optimism is already in the price. Two identical businesses at 12x and 40x are not the same investment, whatever the scorecard says about either.",
        "formula": "Share price ÷ earnings per share",
        "read_it": "There is no universally good number. Compare a company to its own history and to close competitors. A high multiple says the market expects growth; the risk you are taking is that the growth does not arrive.",
        "traps": [
            "It is useless when earnings are negative or near zero, and unstable for cyclical companies — the lowest P/E in a cycle often comes at the peak, just before earnings fall.",
            "Forward P/E depends entirely on estimates being right, and estimates are usually too optimistic at turning points.",
            "A price in one currency over an EPS in another is not a P/E. It is a number.",
        ],
        "see_live": {"surface": "fundamentals", "note": "In the What it costs strip, above the scorecard, and against the company's own history."},
        "related": ["eps", "market-cap", "price-to-fcf", "net-income"],
        "sources": ["https://www.investor.gov/introduction-investing/investing-basics/glossary/price-earnings-ratio"],
    },
    {
        "slug": "price-to-fcf",
        "name": "Price to free cash flow",
        "aka": ["P/FCF", "free cash flow yield"],
        "topic": "valuation", "level": 2,
        "one_liner": "The same idea as P/E, measured against cash rather than accounting profit.",
        "what": "Market capitalisation divided by free cash flow. Inverted, it gives a free cash flow yield: a P/FCF of 25 is a 4% yield.",
        "why": "It resists the accounting choices that shape reported earnings, so it is the more honest multiple for companies with heavy depreciation, large acquisitions or unusual tax positions.",
        "formula": "Market cap ÷ free cash flow. Yield = 1 ÷ (P/FCF)",
        "read_it": "Reading it as a yield makes it comparable to other things you could own. A 2% free cash flow yield is a bet on growth; it is not competitive with a bond on today's cash alone.",
        "traps": [
            "Free cash flow is lumpier than earnings, so one year's multiple can mislead badly. Average across a cycle.",
            "A company mid-way through a large capex programme shows a poor multiple precisely because it is investing.",
        ],
        "see_live": {"surface": "fundamentals", "note": "In the What it costs strip, with the implied yield spelled out."},
        "related": ["free-cash-flow", "pe-ratio", "market-cap"],
        "sources": ["https://www.sec.gov/answers/form10k.htm"],
    },
    {
        "slug": "trailing-twelve-months",
        "name": "Trailing twelve months (TTM)",
        "aka": ["TTM", "LTM", "trailing twelve months"],
        "topic": "valuation", "level": 2,
        "one_liner": "The last four quarters added together, so the figure is current rather than up to a year stale.",
        "what": "A rolling annual figure built from the four most recent quarterly reports rather than the last completed fiscal year. It answers the same question an annual figure does — what did a full year look like — but ends at the most recent quarter instead of at the year end.",
        "why": "A fiscal year that ended eleven months ago describes a company that may have changed completely. TTM keeps an annual-shaped number current.",
        "formula": "Sum of the four most recent quarters",
        "read_it": "Anywhere a TTM figure sits beside annual ones, check it is labelled. This app marks TTM margins on the history table rather than printing them under a fiscal year heading.",
        "traps": [
            "It needs exactly four non-overlapping quarters. Mixing an annual figure with quarterly ones double-counts.",
            "A TTM built across an acquisition blends two different companies.",
            "TTM figures from a data provider may be computed on a different basis than the filings, so a TTM figure and an annual one can disagree for reasons that are not errors.",
        ],
        "see_live": {"surface": "fundamentals", "note": "The newest margin on the history table is TTM and is tagged as such."},
        "related": ["quarterly-report-10q", "gross-margin", "pe-ratio"],
        "sources": ["https://www.sec.gov/answers/form10q.htm"],
    },
    # ── Filings and events ───────────────────────────────────────────────────
    {
        "slug": "earnings-report",
        "name": "The earnings report",
        "aka": ["earnings", "earnings release", "quarterly results", "reporting season"],
        "topic": "events", "level": 1,
        "one_liner": "The quarterly moment a company tells the market how it did — and what it expects next.",
        "what": "Four times a year a company releases its results, usually before the market opens or after it closes, followed by a call where management takes analyst questions. The release is filed with the SEC as an 8-K under item 2.02; the full detail follows in the 10-Q or 10-K.",
        "why": "It is the scheduled moment when opinion meets fact. Most of a stock's largest single-day moves happen on these days, and the move is driven less by the results themselves than by the gap between the results and what was expected.",
        "formula": "",
        "read_it": "Read four things, in order. Revenue and EPS against expectations. Guidance — what management says about the next quarter, which usually moves the stock more than the quarter just reported. Margins, to see whether growth is profitable. Then the call, where the questions analysts ask reveal what the market is actually worried about.",
        "traps": [
            "'Beating expectations' is measured against analyst estimates, not against last year. A company can beat while shrinking.",
            "Companies guide conservatively so they can beat, which makes a beat less meaningful than it sounds.",
            "The headline EPS is often 'adjusted' — the company's own version with costs it deems unrepresentative removed. Compare the adjusted figure to the GAAP one; the gap is the story.",
            "A stock can fall on excellent results if expectations were higher. The result is not the news; the surprise is.",
        ],
        "see_live": {"surface": "calendar", "note": "The catalyst calendar shows which companies report in the next seven days."},
        "related": ["eps", "quarterly-report-10q", "current-report-8k", "guidance"],
        "sources": ["https://www.sec.gov/answers/form8k.htm"],
    },
    {
        "slug": "guidance",
        "name": "Guidance",
        "aka": ["outlook", "forward guidance", "company guidance"],
        "topic": "events", "level": 2,
        "one_liner": "What management says about the quarter that has not happened yet.",
        "what": "A company's own forecast for revenue, earnings or margins in coming periods, usually given alongside results. Some companies guide precisely, some give ranges, some refuse to guide at all.",
        "why": "Markets price the future, so a forecast about the future often moves a stock more than a report about the past. A quarter that beat with cut guidance frequently ends the day down.",
        "formula": "",
        "read_it": "Watch the change more than the level: raised, maintained, or cut relative to what the company said three months ago.",
        "traps": [
            "Guidance is not audited and carries no obligation. It is management's opinion with an incentive attached.",
            "Companies that habitually guide low and beat have trained the market to expect it, so the beat is already in the price.",
            "Withdrawing guidance entirely is usually a stronger signal than any number.",
        ],
        "see_live": {"surface": "calendar", "note": "Earnings dates on the calendar are when guidance arrives."},
        "related": ["earnings-report", "eps"],
        "sources": ["https://www.sec.gov/answers/form8k.htm"],
    },
    {
        "slug": "restatement",
        "name": "Restatement",
        "aka": ["non-reliance", "8-K item 4.02", "restated financials"],
        "topic": "events", "level": 2,
        "one_liner": "The company saying its own previously published numbers cannot be relied on.",
        "what": "A company files an 8-K under item 4.02 when it concludes that previously issued financial statements should no longer be relied upon. The figures are later corrected and refiled.",
        "why": "It is categorically different from a bad ratio. Every other analysis you have done on that company was built on figures the company has now withdrawn. This app drops the verdict a full band when it sees one, regardless of the score.",
        "formula": "",
        "read_it": "Find out what was restated and by how much. A correction to a footnote disclosure is not the same as a correction to revenue.",
        "traps": [
            "Restatements are often disclosed quietly, late on a Friday, in a filing with no headline.",
            "The corrected figures may take months to arrive, during which every screener still shows the old ones.",
        ],
        "see_live": {"surface": "fundamentals", "note": "The Filed disclosures panel watches for item 4.02 and downgrades on it."},
        "related": ["current-report-8k", "annual-report-10k", "earnings-quality"],
        "sources": ["https://www.sec.gov/answers/form8k.htm"],
    },
    # ── The macro calendar ───────────────────────────────────────────────────
    {
        "slug": "cpi",
        "name": "Consumer Price Index (CPI)",
        "aka": ["CPI", "inflation report", "core CPI"],
        "topic": "macro", "level": 1,
        "one_liner": "The monthly inflation reading — how much more a basket of everyday things costs than it did a year ago.",
        "what": "The Bureau of Labor Statistics tracks the price of a fixed basket of goods and services bought by urban households — housing, food, energy, transport, medical care — and reports how it changed. Core CPI strips out food and energy, which are volatile for reasons unrelated to the underlying trend.",
        "why": "Inflation determines interest rates, and interest rates determine what every future dollar of company earnings is worth today. That is why one number released at 8:30 in the morning can move the entire market at once, including companies it has nothing to do with.",
        "formula": "",
        "read_it": "There are two figures: month-on-month and year-on-year, headline and core. Core year-on-year is what policymakers weight most heavily. What moves markets is not the level but the gap against what economists expected — an in-line hot number can be a non-event and a small surprise can be violent.",
        "traps": [
            "Shelter is roughly a third of the index and is measured with a long lag, so CPI can keep describing a housing market that has already turned.",
            "A single month is noise. The three-month annualised rate is the more honest read on direction.",
            "The reaction is to the surprise, not the number. Knowing CPI came in at 3% tells you nothing about the market's move without knowing what was expected.",
        ],
        "see_live": {"surface": "calendar", "note": "On the catalyst calendar with its release date and time, from the BLS schedule."},
        "related": ["ppi", "pce", "fed-funds-rate", "vix"],
        "sources": ["https://www.bls.gov/cpi/"],
    },
    {
        "slug": "ppi",
        "name": "Producer Price Index (PPI)",
        "aka": ["PPI", "producer inflation"],
        "topic": "macro", "level": 2,
        "one_liner": "Inflation measured at the factory gate rather than the checkout.",
        "what": "The Bureau of Labor Statistics measure of prices received by domestic producers for their output — the wholesale side of the economy.",
        "why": "Costs move upstream before they reach consumers, so PPI is watched as a leading indicator of CPI. It also reads directly on corporate margins: producers absorbing rising input costs without raising prices are watching their gross margin compress.",
        "formula": "",
        "read_it": "Read it alongside CPI. PPI rising faster than CPI means producers are eating costs; the reverse means they are passing them on and expanding margins.",
        "traps": [
            "The pass-through to consumer prices is loose and slow, so PPI is a weaker predictor than its reputation suggests.",
            "It is heavily weighted toward goods, so it says less in a services-dominated economy.",
        ],
        "see_live": {"surface": "calendar", "note": "On the catalyst calendar, usually within a day or two of CPI."},
        "related": ["cpi", "gross-margin", "pce"],
        "sources": ["https://www.bls.gov/ppi/"],
    },
    {
        "slug": "pce",
        "name": "PCE price index",
        "aka": ["PCE", "core PCE", "personal consumption expenditures"],
        "topic": "macro", "level": 2,
        "one_liner": "The inflation gauge the Federal Reserve actually targets.",
        "what": "The Bureau of Economic Analysis price index for personal consumption expenditures, released with the monthly personal income and outlays report. It differs from CPI in what it covers and in letting the basket shift as people substitute between goods.",
        "why": "The Federal Reserve's 2% inflation target is defined on PCE, not CPI. When the two disagree, PCE is the one policy responds to.",
        "formula": "",
        "read_it": "Core PCE year-on-year is the single figure closest to what the Fed is steering by. It typically runs a little below CPI because of how the two handle substitution and housing.",
        "traps": [
            "It arrives weeks after CPI for the same month, so it is often already priced by the time it lands.",
            "Watching CPI and assuming the Fed sees the same number is a common and consequential mistake.",
        ],
        "see_live": {"surface": "calendar", "note": "On the catalyst calendar, from the BEA release schedule."},
        "related": ["cpi", "fed-funds-rate", "ppi"],
        "sources": ["https://www.bea.gov/data/personal-consumption-expenditures-price-index"],
    },
    {
        "slug": "nonfarm-payrolls",
        "name": "Non-farm payrolls (NFP)",
        "aka": ["NFP", "jobs report", "employment situation", "unemployment rate"],
        "topic": "macro", "level": 1,
        "one_liner": "The monthly jobs report — how many jobs the economy added, and how many people cannot find one.",
        "what": "The Bureau of Labor Statistics Employment Situation report, covering payroll jobs added or lost, the unemployment rate, and average hourly earnings. It comes from two separate surveys, which is why the job count and the unemployment rate can point different ways in the same month.",
        "why": "Employment drives consumer spending, which is most of the economy, and wage growth feeds inflation. It is one of the two releases — with CPI — that most reliably moves the whole market at once.",
        "formula": "",
        "read_it": "Three numbers: jobs added against expectations, the unemployment rate, and average hourly earnings. Wage growth matters as much as the job count, because it is the inflation channel.",
        "traps": [
            "Prior months are revised, sometimes heavily. A strong headline with large downward revisions to the two months before it is a weak report.",
            "The job count and the unemployment rate come from different surveys and routinely disagree.",
            "In a market worried about inflation, a strong jobs report can be bad news for stocks, because it makes rate cuts less likely. The sign of the reaction depends on what the market is currently afraid of.",
        ],
        "see_live": {"surface": "calendar", "note": "On the catalyst calendar, from the BLS schedule."},
        "related": ["cpi", "fed-funds-rate", "gdp"],
        "sources": ["https://www.bls.gov/news.release/empsit.toc.htm"],
    },
    {
        "slug": "gdp",
        "name": "Gross domestic product (GDP)",
        "aka": ["GDP", "economic growth"],
        "topic": "macro", "level": 2,
        "one_liner": "The total output of the economy, reported quarterly in three passes.",
        "what": "The Bureau of Economic Analysis measure of everything produced. It is released three times for each quarter — an advance estimate, then a second and a third as more data arrives.",
        "why": "It is the broadest read on whether the economy is expanding or contracting, which sets the backdrop for corporate earnings in aggregate.",
        "formula": "",
        "read_it": "The advance estimate moves markets; the revisions usually do not, because by then the quarter is old news. Watch the composition — growth driven by consumer spending is different in character from growth driven by inventory build.",
        "traps": [
            "It is backward-looking by a full quarter, so markets have often already priced what it reports.",
            "The headline is annualised, so a 0.5% quarterly change is reported as roughly 2%.",
        ],
        "see_live": {"surface": "calendar", "note": "On the catalyst calendar, with the estimate marked."},
        "related": ["nonfarm-payrolls", "fed-funds-rate", "retail-sales"],
        "sources": ["https://www.bea.gov/data/gdp/gross-domestic-product"],
    },
    {
        "slug": "retail-sales",
        "name": "Retail sales",
        "aka": ["advance monthly retail trade", "consumer spending report"],
        "topic": "macro", "level": 2,
        "one_liner": "A direct monthly read on whether consumers are still spending.",
        "what": "The Census Bureau's advance estimate of monthly sales at retail and food service businesses, published about two weeks after the month ends. It is an early estimate built from a sample, which is why it is revised as fuller data arrives.",
        "why": "Consumer spending is the largest single component of the economy, and this is the fastest monthly read on it. It arrives well before the quarterly GDP figure that will eventually confirm it.",
        "formula": "",
        "read_it": "The control group — which strips out cars, petrol, building materials and food service — is the cleaner signal, because those categories swing on prices rather than demand.",
        "traps": [
            "Figures are in dollars, not units, so inflation alone can make a flat month look like growth.",
            "It is volatile month to month and heavily revised, so a single reading is a poor basis for any conclusion about the consumer.",
            "Online sales are captured but the category mix shifts over time, so long-run comparisons of any single category are unreliable.",
        ],
        "see_live": {"surface": "calendar", "note": "On the catalyst calendar, from the Census release schedule."},
        "related": ["gdp", "cpi", "nonfarm-payrolls"],
        "sources": ["https://www.census.gov/retail/index.html"],
    },
    {
        "slug": "fed-funds-rate",
        "name": "The Fed funds rate and the FOMC",
        "aka": ["FOMC", "Fed decision", "interest rates", "dot plot", "federal funds rate"],
        "topic": "macro", "level": 1,
        "one_liner": "The interest rate the Federal Reserve sets, which prices everything else.",
        "what": "The Federal Open Market Committee meets eight times a year and sets a target range for the federal funds rate — the rate banks charge each other overnight. Every other rate in the economy is built on top of it. Four of those meetings also publish a Summary of Economic Projections, known as the dot plot, showing where each participant expects rates to go.",
        "why": "Interest rates are the discount rate on the future. When rates rise, a dollar a company will earn in 2035 is worth less today, and companies whose value is mostly in distant profits fall hardest. This is why a rate decision moves growth stocks more than profitable ones.",
        "formula": "",
        "read_it": "The decision itself is usually anticipated. What moves markets is the statement's wording, the dot plot when there is one, and the press conference half an hour later — where the tone often moves more than the decision did.",
        "traps": [
            "Markets price the expected path, not the current rate. A cut that was fully expected changes nothing; a cut with hawkish commentary can send stocks down.",
            "The Fed sets one overnight rate. Mortgage and corporate borrowing rates follow the bond market, which can move the other way.",
        ],
        "see_live": {"surface": "calendar", "note": "FOMC decisions, projections and press conferences are on the catalyst calendar through 2027."},
        "related": ["cpi", "pce", "vix", "nonfarm-payrolls"],
        "sources": ["https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"],
    },
    # ── Market structure and risk ────────────────────────────────────────────
    {
        "slug": "vix",
        "name": "The VIX",
        "aka": ["VIX", "volatility index", "fear gauge"],
        "topic": "market", "level": 1,
        "one_liner": "What options prices imply about how much the S&P 500 will move over the next 30 days.",
        "what": "The Cboe Volatility Index, derived from the prices of S&P 500 index options. It is not a forecast anyone makes by hand — it is extracted from what traders are currently paying for protection. It is quoted as an annualised percentage.",
        "why": "It is the market's own estimate of how uncertain the next month is, updated continuously. It is called the fear gauge because demand for protection rises when people are frightened, and it reliably rises when the market falls.",
        "formula": "Rough daily equivalent: VIX ÷ 16 ≈ the expected daily move in percent (16 is approximately the square root of the number of trading days in a year)",
        "read_it": "Divide by 16 to make it intuitive. A VIX of 16 implies roughly a 1% daily move; a VIX of 32 implies about 2%. Low teens is a calm market, the 20s means the market is nervous, and sustained above 30 means something is genuinely wrong.",
        "traps": [
            "It measures expected size of movement, not direction. A high VIX does not predict a fall — it predicts a large move either way, though in practice the two correlate because falls are faster than rises.",
            "It is an implied figure, not a forecast that has to come true. Realised volatility usually comes in below it, which is why selling protection is profitable most of the time and occasionally catastrophic.",
            "A low VIX is not safety. Calm periods are when leverage builds, and the largest volatility spikes start from low readings.",
        ],
        "see_live": {"surface": "intel", "note": "Market context on the intel page reads the volatility regime."},
        "related": ["beta", "fed-funds-rate", "cpi"],
        "sources": ["https://www.cboe.com/tradable_products/vix/"],
    },
    {
        "slug": "beta",
        "name": "Beta",
        "aka": ["market sensitivity", "systematic risk"],
        "topic": "market", "level": 2,
        "one_liner": "How much a stock tends to move when the whole market moves.",
        "what": "A statistical measure of how a stock's returns have historically moved relative to the market. A beta of 1 moved with the index; 1.5 moved half again as much in both directions; below 1 moved less.",
        "why": "It tells you what a position does to the volatility of everything you own. A portfolio of high-beta names is a leveraged bet on the market whether or not you borrowed anything.",
        "formula": "",
        "read_it": "As a description of the past, not a promise about the future. It is most useful for understanding how a whole portfolio will behave in a drawdown.",
        "traps": [
            "It is backward-looking and unstable — it changes with the period measured and the index compared against.",
            "It captures market-wide risk only. A company facing a lawsuit that could end it may have a perfectly ordinary beta.",
            "Low beta is not low risk. It means uncorrelated with the market, which is a different thing entirely.",
        ],
        "see_live": {"surface": "intel", "note": "Reported with market context where the data provider supplies it."},
        "related": ["vix", "market-cap"],
        "sources": ["https://www.investor.gov/introduction-investing/investing-basics/glossary/beta"],
    },
]

# ── Lookup ───────────────────────────────────────────────────────────────────

BY_SLUG: dict[str, dict] = {c["slug"]: c for c in CONCEPTS}

# Scorecard row key -> concept slug. The scorecard names a row for the test it
# performs ("cash_covers_debt"); the library names a concept for the idea
# ("cash-coverage"). Keeping the two vocabularies separate means a row can be
# renamed without renaming the idea, and one concept can back several rows.
ROW_CONCEPTS: dict[str, str] = {
    "current_ratio":            "current-ratio",
    "debt_to_equity":           "debt-to-equity",
    "cash_covers_debt":         "cash-coverage",
    "retained_earnings_growth": "retained-earnings",
    "goodwill_ratio":           "goodwill",
    "revenue_growth":           "revenue-growth",
    "gross_margin":             "gross-margin",
    "operating_margin":         "operating-margin",
    "net_margin":               "net-margin",
    "eps_growth":               "eps-growth",
    "fcf_positive":             "free-cash-flow",
    "fcf_vs_net_income":        "earnings-quality",
    "ocf_trend":                "operating-cash-flow",
    "capex_ratio":              "capex",
    "debt_financing":           "financing-cash-flow",
    "roe":                      "roe",
    "roic":                     "roic",
    "moat":                     "moat",
    "share_dilution":           "share-dilution",
}


def get(slug: str) -> dict | None:
    """One concept by slug."""
    return BY_SLUG.get((slug or "").strip().lower())


def for_row(row_key: str) -> dict | None:
    """The concept behind a scorecard row."""
    return get(ROW_CONCEPTS.get(row_key, ""))


def by_topic() -> list[dict]:
    """Topics in display order, each with its concepts ordered by level."""
    out = []
    for key, meta in TOPICS.items():
        items = sorted((c for c in CONCEPTS if c["topic"] == key),
                       key=lambda c: (c["level"], c["name"]))
        if items:
            out.append({"key": key, **meta, "concepts": items})
    return out


def search(query: str, limit: int = 20) -> list[dict]:
    """Concepts matching a query, best first.

    Ranked rather than filtered: an exact name or alias beats a name that
    starts with the query, which beats a mention anywhere in the body. Someone
    typing "eps" wants the EPS card, not the eleven concepts that mention it.
    """
    q = (query or "").strip().lower()
    if not q:
        return []
    scored: list[tuple] = []
    for concept in CONCEPTS:
        names = [concept["name"].lower()] + [a.lower() for a in concept.get("aka", [])]
        if q in names or q == concept["slug"]:
            rank = 0
        elif any(n.startswith(q) for n in names):
            rank = 1
        elif any(q in n for n in names):
            rank = 2
        elif q in concept["one_liner"].lower():
            rank = 3
        elif q in concept["what"].lower() or q in concept.get("why", "").lower():
            rank = 4
        else:
            continue
        scored.append((rank, concept["name"], concept))
    scored.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in scored[:limit]]


def related_to(slug: str) -> list[dict]:
    """Resolved related concepts, skipping any that no longer exist."""
    concept = get(slug)
    if not concept:
        return []
    return [BY_SLUG[s] for s in concept.get("related", []) if s in BY_SLUG]


# ── Compatibility with the original inline education dict ────────────────────
# The scorecard rows have always carried a def/why/formula triple. That dict is
# now a view over the concept store rather than a second copy of the content,
# so the "?" on a row and the library page can never drift apart.
EDUCATION: dict[str, dict] = {
    row: {
        "def": concept["what"],
        "why": concept["why"],
        "formula": concept["formula"],
        # New fields the row expander can use; older templates ignore them.
        "slug": concept["slug"],
        "name": concept["name"],
        "one_liner": concept["one_liner"],
        "read_it": concept["read_it"],
        "traps": concept["traps"],
    }
    for row, slug in ROW_CONCEPTS.items()
    if (concept := BY_SLUG.get(slug)) is not None
}
