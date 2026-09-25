package com.wifiquality.monitor;

import android.content.Context;
import android.graphics.Canvas;
import android.graphics.Paint;
import android.graphics.RectF;
import android.view.View;

/** 電波強度 (dBm) を横棒グラフで描画する View。-100dBm で 0、-30dBm で最大。 */
public class SignalBarView extends View {

    private static final int MIN_DBM = -100;
    private static final int MAX_DBM = -30;

    private final Paint trackPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint barPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final Paint textPaint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private final RectF rect = new RectF();
    private final float density;
    private int rssi = MIN_DBM;

    public SignalBarView(Context context) {
        super(context);
        density = context.getResources().getDisplayMetrics().density;
        trackPaint.setColor(0xFF333840);
        textPaint.setColor(0xFFFFFFFF);
        textPaint.setTextSize(12 * density);
        textPaint.setFakeBoldText(true);
        textPaint.setShadowLayer(2 * density, 0, 0, 0xFF000000);
    }

    public void setRssi(int rssi) {
        this.rssi = rssi;
        barPaint.setColor(colorForRssi(rssi));
        invalidate();
    }

    public static int colorForRssi(int rssi) {
        if (rssi >= -55) return 0xFF2ECC71; // 緑
        if (rssi >= -65) return 0xFF9ACD32; // 黄緑
        if (rssi >= -75) return 0xFFF1C40F; // 黄
        if (rssi >= -85) return 0xFFE67E22; // 橙
        return 0xFFE74C3C;                  // 赤
    }

    @Override
    protected void onMeasure(int widthMeasureSpec, int heightMeasureSpec) {
        int h = (int) (18 * density);
        setMeasuredDimension(MeasureSpec.getSize(widthMeasureSpec), h);
    }

    @Override
    protected void onDraw(Canvas canvas) {
        float w = getWidth();
        float h = getHeight();
        float r = 3 * density;
        rect.set(0, 0, w, h);
        canvas.drawRoundRect(rect, r, r, trackPaint);

        float ratio = (float) (rssi - MIN_DBM) / (MAX_DBM - MIN_DBM);
        ratio = Math.max(0.02f, Math.min(1f, ratio));
        rect.set(0, 0, w * ratio, h);
        canvas.drawRoundRect(rect, r, r, barPaint);

        String label = rssi + " dBm";
        float baseline = h / 2 - (textPaint.descent() + textPaint.ascent()) / 2;
        canvas.drawText(label, 6 * density, baseline, textPaint);
    }
}
