import Foundation
import Testing
@testable import RDTeamAgentCore

@Suite struct AppConfigTests {
    @Test func defaultsAreValid() {
        #expect(AppConfig.defaults.validationErrors().isEmpty)
        #expect(AppConfig.defaults.region == "us-east-1")
    }

    @Test func invocationsURLEncodesTheWholeArn() {
        let url = AppConfig.defaults.invocationsURL?.absoluteString
        #expect(url == "https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/"
            + "arn%3Aaws%3Abedrock-agentcore%3Aus-east-1%3A954976326201%3Aruntime%2Frd_team_agent-QTcxE482tI"
            + "/invocations?qualifier=DEFAULT")
    }

    @Test func badFieldsAreReported() {
        let config = AppConfig(runtimeArn: "arn:aws:s3:::bucket", qualifier: "bad qualifier",
                               loginDomain: "http://example.com/path", clientId: "abc-123")
        #expect(config.validationErrors().count == 4)
        #expect(config.invocationsURL == nil)
        #expect(config.loginBaseURL == nil)
    }

    @Test func trimmingDropsWhitespaceAndTrailingSlash() {
        let config = AppConfig(runtimeArn: "  \(AppConfig.defaults.runtimeArn)\n", qualifier: " DEFAULT ",
                               loginDomain: " \(AppConfig.defaults.loginDomain)/ ", clientId: " abc ")
        let trimmed = config.trimmed()
        #expect(trimmed.runtimeArn == AppConfig.defaults.runtimeArn)
        #expect(trimmed.loginDomain == AppConfig.defaults.loginDomain)
        #expect(trimmed.clientId == "abc")
        #expect(trimmed.qualifier == "DEFAULT")
    }
}

@Suite struct PKCETests {
    @Test func rfc7636Vector() {
        let pkce = PKCE(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", state: "s")
        #expect(pkce.challenge == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")
    }

    @Test func generatedValuesAreUrlSafeAndFresh() {
        let first = PKCE.generate()
        let second = PKCE.generate()
        #expect(first.verifier.count == 43)
        #expect(first.verifier != second.verifier)
        #expect(first.state != second.state)
        let allowed = CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
        #expect(first.verifier.unicodeScalars.allSatisfy { allowed.contains($0) })
    }
}

@Suite struct CognitoAuthTests {
    let pkce = PKCE(verifier: "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk", state: "state123")

    @Test func authorizeURLCarriesCodeFlowAndPKCE() throws {
        let url = try #require(CognitoAuth.authorizeURL(config: .defaults, pkce: pkce))
        let components = try #require(URLComponents(url: url, resolvingAgainstBaseURL: false))
        #expect(components.host == "rd-team-agent-954976326201.auth.us-east-1.amazoncognito.com")
        #expect(components.path == "/oauth2/authorize")
        let items = Dictionary(uniqueKeysWithValues: (components.queryItems ?? []).map { ($0.name, $0.value ?? "") })
        #expect(items["response_type"] == "code")
        #expect(items["client_id"] == AppConfig.defaults.clientId)
        #expect(items["redirect_uri"] == "rdteamagent://callback")
        #expect(items["scope"] == "openid email profile")
        #expect(items["state"] == "state123")
        #expect(items["code_challenge"] == pkce.challenge)
        #expect(items["code_challenge_method"] == "S256")
    }

    @Test func callbackGivesTheCodeOnlyForOurState() throws {
        let good = URL(string: "rdteamagent://callback?code=abc&state=state123")!
        #expect(try CognitoAuth.authorizationCode(from: good, expectedState: "state123") == "abc")

        let foreign = URL(string: "rdteamagent://callback?code=abc&state=other")!
        #expect(throws: AuthError.stateMismatch) {
            try CognitoAuth.authorizationCode(from: foreign, expectedState: "state123")
        }
        let denied = URL(string: "rdteamagent://callback?error=access_denied&state=state123")!
        #expect(throws: AuthError.loginPage("access_denied")) {
            try CognitoAuth.authorizationCode(from: denied, expectedState: "state123")
        }
        let empty = URL(string: "rdteamagent://callback?state=state123")!
        #expect(throws: AuthError.missingCode) {
            try CognitoAuth.authorizationCode(from: empty, expectedState: "state123")
        }
    }

    @Test func tokenRequestsAreFormEncoded() throws {
        let exchange = try #require(CognitoAuth.codeExchangeRequest(config: .defaults, code: "c/d+e", pkce: pkce))
        #expect(exchange.url?.path == "/oauth2/token")
        #expect(exchange.httpMethod == "POST")
        #expect(exchange.value(forHTTPHeaderField: "Content-Type") == "application/x-www-form-urlencoded")
        let body = String(decoding: try #require(exchange.httpBody), as: UTF8.self)
        #expect(body.contains("grant_type=authorization_code"))
        #expect(body.contains("code=c%2Fd%2Be"))
        #expect(body.contains("redirect_uri=rdteamagent%3A%2F%2Fcallback"))
        #expect(body.contains("code_verifier=\(pkce.verifier)"))

        let refresh = try #require(CognitoAuth.refreshRequest(config: .defaults, refreshToken: "r1"))
        let refreshBody = String(decoding: try #require(refresh.httpBody), as: UTF8.self)
        #expect(refreshBody == "grant_type=refresh_token&client_id=\(AppConfig.defaults.clientId)&refresh_token=r1")

        let revoke = try #require(CognitoAuth.revokeRequest(config: .defaults, refreshToken: "r1"))
        #expect(revoke.url?.path == "/oauth2/revoke")
    }

    @Test func tokenResponseKeepsThePreviousRefreshToken() throws {
        let now = Date(timeIntervalSince1970: 1_000)
        let first = try CognitoAuth.parseTokenResponse(
            Data(#"{"access_token":"a1","id_token":"i1","refresh_token":"r1","expires_in":3600}"#.utf8), now: now)
        #expect(first == TokenSet(accessToken: "a1", idToken: "i1", refreshToken: "r1", expiresAt: now.addingTimeInterval(3600)))

        let refreshed = try CognitoAuth.parseTokenResponse(
            Data(#"{"access_token":"a2","expires_in":3600}"#.utf8), now: now, previousRefreshToken: "r1")
        #expect(refreshed.refreshToken == "r1")
        #expect(refreshed.accessToken == "a2")

        #expect(throws: AuthError.invalidTokenResponse) {
            try CognitoAuth.parseTokenResponse(Data(#"{"expires_in":3600}"#.utf8))
        }
    }

    @Test func expirySoonUsesTheMargin() {
        let now = Date()
        let tokens = TokenSet(accessToken: "a", idToken: nil, refreshToken: "r", expiresAt: now.addingTimeInterval(200))
        #expect(tokens.expiresSoon(now: now))
        #expect(!tokens.expiresSoon(now: now, margin: 100))
    }

    @Test func identityComesFromTheAccessTokenPayload() {
        let payload = PKCE.base64URL(Data(#"{"sub":"a4b894d8-a011","username":"tester1","token_use":"access"}"#.utf8))
        let token = "e30.\(payload).sig"
        #expect(CognitoAuth.identity(fromAccessToken: token) == UserIdentity(userId: "a4b894d8-a011", username: "tester1"))
        #expect(CognitoAuth.identity(fromAccessToken: "not-a-jwt") == nil)
    }

    @Test func tokenErrorTextPrefersTheOAuthError() {
        #expect(CognitoAuth.errorText(Data(#"{"error":"invalid_grant"}"#.utf8)) == "invalid_grant")
        #expect(CognitoAuth.errorText(Data()) == "无内容")
    }
}
