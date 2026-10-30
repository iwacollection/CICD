package com.example.cicd;

public final class App {
    private App() {
    }

    public static String status() {
        return "java17-maven-consumer-ok";
    }

    public static void main(String[] args) {
        String version = System.getProperty("java.specification.version");
        if (!"17".equals(version)) {
            throw new IllegalStateException("Expected Java 17 runtime, got " + version);
        }
        System.out.println(status());
    }
}
