"""Tables I (continuation), II, III and IV transcribed against scans 148–150.

The printed side-by-side columns are stacked into readable rows so the tables
fit the page width without horizontal scrolling. Printed totals are preserved
even where they do not equal the sum of the visible rows.
"""

def _rows(header, rows):
    return header + '\n' + '\n'.join('\t'.join(str(c) for c in row) for row in rows)

H4 = 'Place\tDebtors\tFelons etc.\tPetty Offenders'
H5 = 'Place\t1787 Debts.\t1787 Crim.\t1788 Debts.\t1788 Crim.'
HI = 'Place\tDebtors Men\tWom.\tFelons etc.\tPetty Offenders'
HT = 'Place\tGaol Deliveries and Great Sessions\tQuarter Sessions\tTotal of both\tYearly Average'

T148_LEFT = _rows(HI, [
    ['Brought forward',771,35,79,225],
    ['Marshalsea',65,6,0,0],
    ['Newgate',101,12,224,0],
    ['New Ludgate',14,1,0,0],
    ['Poultry Compter',30,0,19,0],
    ['Savoy',0,0,0,56],
    ['Tothill-Fields Bridewell',4,2,0,86],
    ['Wood-Street Compter',34,0,36,0],
    ['In the hulks',0,0,194,0],
    ['Total',1019,56,552,367],
]) + '\n\nTABLE II\n\nAn account of the number of prisoners in the gaols and prisons of Ireland at the times they were visited in 1787 and 1788.\n\nIn this table I have omitted mentioning those prisons in which, at the times of my visiting in those two years, there were no persons confined.\n\nN.B. Where ciphers are opposite a name, it signifies that the prison was not visited that year.\n\n' + _rows(H5, [
    ['Armagh',2,18,0,0],['Carlow',0,0,4,5],['Carrickfergus',5,21,0,0],['Town',0,1,0,0],
    ['Carrick on Shannon',0,0,2,4],['Castlebar',0,0,4,6],['Cavan',1,36,0,0],['Clonmell',10,51,19,62],
    ['Cork',31,64,23,73],['City',67,45,76,49],['Bridewell',2,7,4,5],['Downpatrick',9,26,0,0],
    ['Dublin Bridewell',0,6,0,12],['City Marshalsea',92,0,66,0],['Four C. Marshalsea',150,0,180,0],
    ['Newgate',1,190,0,220],['Police Prison',0,32,0,12],['Thos. Court & Donor',2,0,0,0],
    ['Dundalk',5,8,0,0],['Ennis',0,0,6,19],['Enniskillen',3,15,0,0],['Galway',4,14,0,0],['City',7,12,0,0],
    ['Kilkenny',2,13,0,0],['City',3,7,0,0],['Kilmainham',7,27,8,47],['Lifford',12,20,0,0],
    ['Limerick',6,19,0,0],['City',12,26,7,10],['Londonderry',12,23,0,0],['Longford',8,8,12,20],
    ['Loughrea Bridewell',0,0,0,3],['Maryborough',2,11,1,7],['Monaghan',13,33,0,0],['Mullingar',2,24,1,19],
    ['Naas',3,19,0,0],['Omagh',16,19,0,0],['Philipstown',0,0,3,3],['Roscommon',1,33,3,13],['Sligo',0,0,2,19],
    ['Tralee',0,0,5,35],['Trim',0,0,5,25],['Waterford',2,6,2,4],['City',3,7,4,7],['Wexford',0,0,7,12],['Wicklow',0,0,1,9],
]) + '\n\nI have visited all the county gaols, and almost all the other prisons; yet, as there are some few of the town prisons which I did'

T148_RIGHT = 'not see, if we add twenty to the number of prisoners, we shall probably have nearly the average number of persons confined at one time in the prisons in Ireland, viz.: Debtors, 550; Criminals, 1004; Supposed omitted, 20; Total, 1574.\n\nN.B. Where I have visited twice, viz., in 1787 and 1788, I have taken, in this estimate, the last number.\n\nTABLE III\n\nAn account of the number of prisoners in the gaols and prisons of England and Wales at the last time they were visited in 1787 or 1788.\n\nAs some county gaols are also the county bridewells, where petty offenders are not always separated from felons, in those places I sometimes include petty offenders under the heading felons, etc.—In this table I have omitted mentioning all those prisons in which, at the times of my visiting in these two years, there were no persons confined.\n\n' + _rows(H4, [
    ['Appleby',1,6,0],['Bridewell',0,0,1],['Aston',0,0,5],['Aylesbury',11,24,0],['Bridewell',0,0,10],
    ['Banbury',0,0,3],['Barking',0,0,2],['Bath',2,6,0],['Batley',22,0,0],['Beaumaris',1,4,0],
    ['Beccles',0,0,3],['Bedford',6,7,0],['Bridewell',0,0,7],['Berwick',3,0,0],['Beverley',0,0,9],
    ['Town',0,0,3],['Hall-garth',1,0,0],['Birmingham',0,0,13],['Court Prison',0,0,7],['Bodmin',18,5,24],
    ['Brecon',10,11,0],['Bridgewater',3,0,0],['Bristol',24,43,0],['Bridewell',0,0,14],["Lawford's-gate",0,0,5],
    ['Bury St. Edmunds',7,17,0],['Bridewell',0,0,3],['Cambridge',7,8,0],['Bridewell',0,0,13],['Town',2,1,0],
    ['Bridewell',0,0,9],['Cardiff',23,7,0],['Total',141,139,131],
    ['Carlisle',32,20,2],['Carmarthen',5,7,2],['Town',0,2,2],['Carnarvon',3,0,0],['Chelmsford',31,76,0],
    ['Bridewell',0,0,30],['Chester',19,37,0],['City',8,9,0],['Bridewell',0,0,2],['Chesterfield Bridewell',0,0,4],
    ['Chichester',0,0,2],['Colchester',0,0,3],['Bridewell',0,0,5],['Coventry',10,10,0],['Bridewell',0,0,8],
    ['Cowbridge',0,0,6],['Dartford',0,0,9],['Derby',7,11,0],['Town',1,2,0],['Devizes',1,0,29],
    ['Dorchester',23,8,0],['Durham',16,13,0],['Bridewell',0,0,10],['Ely',2,3,0],['Bridewell',0,0,1],
    ['Exeter',0,23,2],["Sheriff's Ward",44,0,0],['Bridewell',0,0,22],['City',2,11,0],['Flint',4,3,1],
    ['Falkingham',0,0,3],['Total',208,235,143],
])

T149_LEFT = _rows(H4, [
    ['Gloucester',24,57,0],['Bridewell',0,0,5],['City',5,6,0],['Gosport',0,0,2],['Guildford',0,0,8],
    ['Halifax',4,0,0],['Haverfordwest',3,4,0],['Hereford',6,25,0],['Bridewell',0,0,5],['City',2,0,1],
    ['Hertford',8,3,0],['Bridewell',0,0,3],['Horsham',16,15,0],['Hull',12,4,0],['Bridewell',1,0,3],
    ['Huntingdon',4,9,0],['Bridewell',0,0,2],['Ipswich',15,18,0],['Bridewell',0,0,9],['Ivelchester',57,17,0],
    ['Kendal Bridewell',0,0,6],['Kingston',4,0,0],['Bridewell',0,0,24],['Lancaster',53,65,0],['Launceston',0,3,0],
    ['Leeds',0,0,1],['Leicester',15,14,0],['Bridewell',0,0,9],['Town',1,1,0],['Lincoln',20,29,0],
    ['City',0,5,0],['Bridewell',0,0,3],['Liverpool',18,22,0],['Bridewell',0,0,15],['London Bridewell',0,0,29],
    ['Borough Compter',11,0,0],['Clerkenwell',0,167,0],['Bridewell',3,0,234],['Fleet',212,0,0],["King's Bench",444,0,0],
    ['Marshalsea',37,0,0],['Newgate',114,499,0],['New Ludgate',24,0,0],['Poultry Compter',35,19,0],['Savoy',0,0,45],
    ['Tothill-fields Bridewell',3,0,135],['Tower Hamlets',0,0,1],['Westminster Prison',0,0,94],['Wood Street Compter',44,48,0],
    ['Total',1195,1030,634],
    ['Lostwithiel',5,0,0],['Ludlow',6,0,1],['Macclesfield',6,0,0],['Maidstone',31,80,0],['Bridewell',0,0,25],
    ['Manchester',0,0,53],['Marlborough',0,0,5],['Middlewich',0,0,24],['Monmouth',6,10,0],['Town',0,2,0],
    ['Montgomery',9,12,0],['Bridewell',0,0,0],['Morpeth',15,12,0],['Bridewell',0,0,2],['Nantwich',0,0,2],
    ['Newcastle',14,10,0],['Bridewell',0,0,6],['Newport, Essex',0,0,13],['Northampton',9,20,0],['Town',0,1,0],
    ['Norwich',20,20,0],['City',16,10,0],['Bridewell',0,0,12],['Nottingham',12,19,0],['Bridewell',0,0,3],
    ['Town',5,0,1],['Oakham',0,0,1],['Odiam',0,0,2],['Oundle',0,0,1],['Oxford',12,24,0],
    ['City Bridewell',0,0,7],['Peterborough',0,1,0],['Bridewell',0,0,1],['Petworth',0,0,5],['Plymouth',0,0,3],
    ['Portsmouth',3,8,0],['Presteign',5,2,0],['Preston',1,0,37],['Reading',10,18,0],['Bridewell',0,0,17],
    ['Ruthin',12,9,0],['Bridewell',0,0,2],["St. Alban's Borough gaol",0,1,0],['Liberty',0,6,0],["St. George's Fields Bridewell",0,0,34],
    ['Salisbury',13,17,0],['Sheffield',16,0,0],['Town',0,0,1],['Shepton-Mallet',0,0,35],['Sherborn',0,0,9],
    ['Total',220,282,302],
])

T149_RIGHT = _rows(H4, [
    ['Shrewsbury',25,29,0],['Bridewell',0,0,14],["Southampton Sheriff's Ward",2,0,0],['Town',0,2,0],
    ['Southwark',26,55,0],['Southwell',0,0,11],['Stafford',31,61,0],['Bridewell',0,0,18],['Taunton',0,0,14],
    ['Thirsk',0,0,11],['Usk',0,0,4],['Wakefield',0,0,54],['Warwick',31,60,0],['Bridewell',0,0,10],['Total',115,207,136],
    ['Winchester',31,30,0],['Bridewell',0,0,26],['Windsor Castle',2,0,0],['Town',0,1,0],['Worcester',23,47,0],
    ['Bridewell',0,0,13],['City',12,15,0],['Wymondham',0,0,16],['Yarmouth',1,9,0],['Bridewell',0,0,1],
    ['York',50,51,0],['City',10,6,0],["St. Peter's",3,0,0],['Bridewell',0,0,10],['Total',132,159,66],
]) + '\n\nFrom foregoing pp.\t141\t139\t131\t208\t235\t143\n\t1195\t1030\t634\t220\t282\t302\nGreat Total\t1451\t1376\t901\tAnd\t560\t676\t511\n\nTotal number of Debtors 2011; Felons, etc. 2052; Petty Offenders 1412. Total number of Prisoners in the hulks 1937; supposed omitted 70. Total number of prisoners 7482.\n\nThough I have visited all the county gaols in England, and almost all the other prisons, yet as there are some few of the corporation prisons which I did not see, I have added seventy more prisoners to the number; so that, probably, we have nearly the average number of persons confined at one time in all the prisons of this kingdom.\n\nI subjoin the numbers confined in the London prisons and in the hulks, at the time they were visited in 1788, which are as follows:\n\n' + _rows(H4, [
    ['Bridewell',0,0,29],['Borough Compt.',11,0,0],['Clerkenwell',0,167,0],['Bridewell',3,0,234],
    ['Fleet',212,0,0],["King's Bench",444,0,0],['Marshalsea',37,0,0],['Newgate',114,499,0],['Total',821,666,263],
    ['New Ludgate',24,0,0],['Poultry Compt.',35,19,0],['Savoy',0,0,45],['Tothill-fields Bridewell',3,0,135],
    ['Tower Hamlets',0,0,1],['Westminster Prison',0,0,94],['Wood Street Compter',44,48,0],['In the Hulks',0,1937,0],
    ['Total',106,2004,275],['Great total',927,2670,538],
])

T150_LEFT = 'TABLE IV\n\nAn account of the number of convicts ordered for transportation, from the 1st of November, 1769, to the 1st of November, 1776; extracted from the lists transmitted to the Clerk of the House of Commons, and distinguishing:\n\n(1) The number of convicts in that period, sent from the Old Bailey in London and Middlesex; the assizes in each county of England, where any are held; and the great sessions in Chester and Wales.\n(2) The like from the quarter sessions.\n(3) Totals of both.\n(4) The annual average during that period, being really only six years and an half; the practice of transportation having in great measure ceased before the 1st of May, 1776.\n\nHOME CIRCUIT\n\n' + _rows(HT, [
    ['Essex',106,10,116,18],['Hertfordshire',49,5,'',''],["St. Alban's",0,18,72,12],
    ['Kent',116,45,'',''],['Dover',0,3,164,26],['Canterbury',0,6,6,1],
    ['Sussex',34,3,'',''],['Chichester',0,1,38,6],['Surrey',167,83,'',''],['Southwark',0,18,268,42],
]) + '\n\nNORFOLK CIRCUIT\n\n' + _rows(HT, [
    ['Bedfordshire',40,0,40,7],['Buckinghamshire',44,10,54,9],['Cambridgeshire',23,2,25,4],['Ely',8,0,8,2],
    ['Huntingdonshire',17,0,17,3],['Norfolk',70,3,'',''],["King's Lynn",9,9,'',''],['Yarmouth',0,13,95,15],
    ['Norwich',12,0,12,2],['Suffolk',72,4,'',''],['Bury',0,5,'',''],['Ipswich',0,5,86,14],
]) + '\n\nMIDLAND CIRCUIT\n\n' + _rows(HT, [
    ['Derbyshire',27,17,'',''],['Derby',0,5,49,8],['Lincolnshire',50,0,'',''],['Pts. of Holland',0,0,'',''],
    ['Pts. of Kesteven',0,1,'',''],['Pts. of Lindsey',0,10,61,10],['Lincoln',4,0,4,1],
    ['Nottinghamshire',19,20,39,6],['Nottingham',7,7,14,3],['Rutlandshire',8,0,8,2],
    ['Leicestershire',25,0,25,4],['Leicester',9,3,12,2],['Northamptonshire',28,13,41,7],
    ['Warwickshire',112,2,114,18],['Coventry',28,0,28,5],['Total',789,270,1059,171],
]) + '\n\nNORTHERN CIRCUIT\n\n' + _rows(HT, [
    ['Cumberland',11,20,31,5],['Durham',23,13,36,6],['Northumberland',18,9,'',''],['Berwick',0,2,29,5],
    ['Newcastle-on-Tyne',10,17,27,5],['Westmorland',3,0,'',''],['Kirkby in Kendal',0,1,4,1],
    ['Lancashire',44,95,'',''],['Liverpool',0,22,'',''],['Preston',0,1,'',''],['Wigan',0,4,166,26],
    ['Total',395,235,630,104],
])

T150_RIGHT = 'NORTHERN CIRCUIT—continued\n\n' + _rows(HT, [
    ['Yorkshire',124,0,'',''],['East Riding',0,14,'',''],['North Riding',0,4,'',''],['West Riding',0,42,'',''],
    ['Doncaster',0,1,'',''],['Leeds',0,7,'',''],['Pontefract',0,1,'',''],['Richmond',0,1,'',''],
    ['Ripon',0,1,195,30],['York',12,5,17,3],['Kingston-on-Hull',1,8,9,2],
]) + '\n\nWESTERN CIRCUIT\n\n' + _rows(HT, [
    ['Cornwall',53,25,78,12],['Devonshire',104,29,'',''],['Plymouth',0,7,140,22],['Exeter',1,4,5,1],
    ['Dorsetshire',53,11,64,10],['Poole',1,3,4,1],['Hampshire',113,5,118,19],['Southampton',1,5,6,1],
    ['Wiltshire',60,26,'',''],['Salisbury',0,3,89,14],['Somersetshire',120,40,160,25],['Bristol',36,73,109,17],
    ['City of London',699,0,699,107],['Middlesex',1365,394,'',''],['Westminster',0,159,1918,296],
]) + '\n\nOXFORD CIRCUIT\n\n' + _rows(HT, [
    ['Berkshire',57,15,'',''],['Reading',0,7,79,13],['Oxfordshire',53,3,'',''],['Banbury',0,2,'',''],
    ['Oxford',0,4,62,10],['Gloucestershire',119,19,138,22],['Gloucester',7,7,14,3],['Herefordshire',49,0,49,8],
    ['Monmouthshire',44,2,46,8],['Salop',82,12,'',''],['Shrewsbury',0,5,99,16],['Staffordshire',85,11,96,15],
    ['Litchfield',0,3,3,1],['Worcestershire',59,0,59,10],['Worcester',15,0,15,3],
    ['Total',707,174,881,144],['Foregoing page',789,270,1059,171],['Total',1496,444,1940,315],
]) + '\n\nCHESHIRE AND WALES\n\n' + _rows(HT, [
    ['Cheshire',16,3,19,''],['Chester',0,14,14,''],['Anglesey',1,2,3,''],['Brecknockshire',14,0,14,''],
    ['Cardiganshire',3,0,3,''],['Carmarthenshire',8,0,8,''],['Carmarthen',0,0,0,''],['Carnarvonshire',2,1,3,''],
    ['Denbighshire',0,5,5,''],['Flintshire',1,5,6,''],['Glamorganshire',12,0,12,''],['Merionethshire',5,0,5,''],
    ['Montgomeryshire',5,0,5,''],['Pembrokeshire',5,0,5,''],['Haverfordwest',0,0,0,''],['Radnorshire',2,0,2,16],
    ['Total',2680,814,3494,541],['',395,235,630,104],['',3075,1049,4124,645],['',1496,444,1940,315],
    ['Great Total',4571,1493,6064,960],
])

VERIFIED_TABLE_SPREADS_148 = {
    106: {
        's13': ('He employsnot only', 'He employs not only several hands upon the foundation of the charity, but many others; among them his prisoners. His salary is paid out of the charity.\n\n' + _rows('Date\tPrisoners\tDate\tPrisoners', [
            ['1774. 13 Dec.',3,'1779. 23 Sept.','6 and one lunatic'],
            ['1776. 28 Sept.',7,'1782. 7 Feb.',3],
        ])),
        's14': ('Inanother 1776', ''),
    },
    148: {
        's1': ('Debtors\nMen', T148_LEFT),
        's3': ('Felons\nPetty', T148_RIGHT),
    },
    149: {
        's1': ('Debt-\nFelons', T149_LEFT),
        's3': ('bt-Felons', T149_RIGHT),
    },
    150: {
        's2': ('An accountof thenumber', T150_LEFT),
        's4': ('NORTHERN CIRCUIT-', ''),
        's5': ('continued\nWESTERN', T150_RIGHT),
    },
}
