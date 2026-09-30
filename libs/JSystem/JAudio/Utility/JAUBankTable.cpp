#include "JSystem/JAudio/JAUBankTable.h"

void JAUBankTableDictionary::appendBankTable(JSULink<JAUBankTable> *link)
{
    append(link);
}

JASBank *JAUBankTable::getBank(u32 offset) const
{
    return mBankPtrTable.get(offset);
}
