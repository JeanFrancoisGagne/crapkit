// Swift 6.2 (SE-0451) names a function with any text between backticks, spaces
// included, and Swift Testing names its tests that way. Then a function that calls one.
struct OnboardingChecks {
    func `keeps onboarding if the gateway is down`() {
        if gatewayUp {
            show(1)
        }
    }

    func runAll() {
        `keeps onboarding if the gateway is down`()
    }
}
