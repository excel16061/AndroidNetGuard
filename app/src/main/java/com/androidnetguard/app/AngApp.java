package com.androidnetguard.app;

import android.app.Application;

public final class AngApp extends Application {
    @Override
    public void onCreate() {
        super.onCreate();
        Engine.get(this);   // records process_started; resumes a pending app-restart test
    }
}
