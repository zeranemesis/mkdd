#include "JSystem/J3D/J3DVertexData.h"
#include "JSystem/J3D/J3DVertexBuffer.h"
#include "JSystem/J3D/J3DSys.h"
#include "JSystem/J3D/J3DDrawMtxData.h"
#include "JSystem/JKernel/JKRHeap.h"
#include <dolphin/os.h>
#include <string.h>

J3DVertexData::J3DVertexData() {
    mVtxNum = 0;
    mNormNum = 0;
    mColorNum = 0;
    mTexCoordNum = 0;
    mPacketNum = 0;
    mVtxAttrFmtList = nullptr;
    mVtxPos = nullptr;
    mVtxNorm = nullptr;
    mVtxNBT = nullptr;
    for (int i = 0; i < 2; i++) {
        mVtxColor[i] = nullptr;
    }
    for (int i = 0; i < 8; i++) {
        mVtxTexCoord[i] = nullptr;
    }
    mVtxPosFrac = 0;
    mVtxPosType = GX_F32;
    mVtxNrmFrac = 0;
    mVtxNrmType = GX_F32;
}

void J3DVertexBuffer::setVertexData(J3DVertexData* pVtxData) {
    mVtxData = pVtxData;
    mVtxPos[0] = pVtxData->mVtxPos;
    mVtxNorm[0] = pVtxData->mVtxNorm;
    mVtxColor[0] = pVtxData->mVtxColor[0];
    mVtxPos[1] = nullptr;
    mVtxNorm[1] = nullptr;
    mVtxColor[1] = nullptr;
    mTransformedVtxPos[0] = pVtxData->mVtxPos;
    mTransformedVtxNorm[0] = pVtxData->mVtxNorm;
    mTransformedVtxPos[1] = nullptr;
    mTransformedVtxNorm[1] = nullptr;
    frameInit();
}

void J3DVertexBuffer::init() {
    mVtxData = nullptr;
    mVtxPos[1] = nullptr;
    mVtxPos[0] = nullptr;
    mVtxNorm[1] = nullptr;
    mVtxNorm[0] = nullptr;
    mVtxColor[1] = nullptr;
    mVtxColor[0] = nullptr;
    mTransformedVtxPos[1] = nullptr;
    mTransformedVtxPos[0] = nullptr;
    mTransformedVtxNorm[1] = nullptr;
    mTransformedVtxNorm[0] = nullptr;
    mCurrentVtxPos = nullptr;
    mCurrentVtxNorm = nullptr;
    mCurrentVtxColor = nullptr;
    frameInit();
}

J3DVertexBuffer::~J3DVertexBuffer() { }

void J3DVertexBuffer::setArray() const {
    j3dSys.mVtxPos = mCurrentVtxPos;
    j3dSys.mVtxNrm = mCurrentVtxNorm;
    j3dSys.mVtxCol = mCurrentVtxColor;
}

void J3DVertexBuffer::copyLocalVtxArray(J3DDeformAttachFlag flag) {
    u32 f = flag;
    if (~f & 2) {
        if (f & 1) {
            for (int i = 0; i < 2; i++) {
                mVtxPos[i] = new (0x20) char[mVtxData->mVtxNum * 0xC];
                memcpy(mVtxPos[i], mVtxData->mVtxPos, mVtxData->mVtxNum * 0xC);
                DCStoreRange(mVtxPos[i], mVtxData->mVtxNum * 0xC);
            }
        } else {
            mVtxPos[0] = mVtxData->mVtxPos;
            if (mVtxPos[1] == nullptr) {
                mVtxPos[1] = new (0x20) char[mVtxData->mVtxNum * 0xC];
            }
            memcpy(mVtxPos[1], mVtxData->mVtxPos, mVtxData->mVtxNum * 0xC);
            DCStoreRange(mVtxPos[1], mVtxData->mVtxNum * 0xC);
        }
    } else {
        mVtxPos[0] = mVtxPos[1] = mVtxData->mVtxPos;
    }

    if (~f & 4) {
        if (f & 1) {
            for (int i = 0; i < 2; i++) {
                mVtxNorm[i] = new (0x20) char[mVtxData->mNormNum * 0xC];
                memcpy(mVtxNorm[i], mVtxData->mVtxNorm, mVtxData->mNormNum * 0xC);
                DCStoreRange(mVtxNorm[i], mVtxData->mNormNum * 0xC);
            }
        } else {
            mVtxNorm[0] = mVtxData->mVtxNorm;
            if (mVtxNorm[1] == nullptr) {
                mVtxNorm[1] = new (0x20) char[mVtxData->mNormNum * 0xC];
            }
            memcpy(mVtxNorm[1], mVtxData->mVtxNorm, mVtxData->mNormNum * 0xC);
            DCStoreRange(mVtxNorm[1], mVtxData->mNormNum * 0xC);
        }
    } else {
        mVtxNorm[0] = mVtxNorm[1] = mVtxData->mVtxNorm;
    }
}

J3DDrawMtxData::J3DDrawMtxData() {
    mCount = 0;
    mDrawMtxFlag = nullptr;
    mDrawMtxIdx = nullptr;
}

J3DDrawMtxData::~J3DDrawMtxData() { }
