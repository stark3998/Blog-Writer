import { useEffect, useState, createContext, useContext, type ReactNode } from "react";
import {
  PublicClientApplication,
  InteractionRequiredAuthError,
  EventType,
  type AccountInfo,
  type EventMessage,
  type AuthenticationResult,
} from "@azure/msal-browser";
import { MsalProvider, useMsal, useIsAuthenticated } from "@azure/msal-react";
import { msalConfig, loginRequest, isAuthConfigured, hasCustomApiScope } from "./msalConfig";
import { setAccessTokenGetter } from "../services/api";

const msalInstance = new PublicClientApplication(msalConfig);

interface AuthContextValue {
  isAuthenticated: boolean;
  isLoading: boolean;
  user: AccountInfo | null;
  login: () => Promise<void>;
  logout: () => void;
  getAccessToken: () => Promise<string>;
}

const AuthContext = createContext<AuthContextValue>({
  isAuthenticated: false,
  isLoading: true,
  user: null,
  login: async () => {},
  logout: () => {},
  getAccessToken: async () => "",
});

export function useAuth() {
  return useContext(AuthContext);
}

function AuthContextProvider({ children }: { children: ReactNode }) {
  const { instance, accounts } = useMsal();
  const isAuthenticated = useIsAuthenticated();
  const [isLoading, setIsLoading] = useState(true);

  useEffect(() => {
    // Wait for MSAL to fully initialize and handle any redirect response
    instance.initialize().then(() => {
      instance.handleRedirectPromise().then((response) => {
        if (response?.account) {
          instance.setActiveAccount(response.account);
        }
        setIsLoading(false);
      }).catch((err) => {
        console.error("MSAL redirect handling failed:", err);
        setIsLoading(false);
      });
    });

    // Listen for login success events (from popup or redirect)
    const callbackId = instance.addEventCallback((event: EventMessage) => {
      if (event.eventType === EventType.LOGIN_SUCCESS && event.payload) {
        const result = event.payload as AuthenticationResult;
        if (result.account) {
          instance.setActiveAccount(result.account);
        }
      }
    });

    return () => {
      if (callbackId) instance.removeEventCallback(callbackId);
    };
  }, [instance]);

  // Register synchronously during render so child effects never run before
  // the API module can resolve a token getter.
  setAccessTokenGetter(getAccessToken);

  const user = accounts[0] ?? null;

  async function login() {
    try {
      await instance.loginRedirect(loginRequest);
    } catch (err) {
      console.error("Login failed:", err);
    }
  }

  function logout() {
    instance.logoutRedirect({ postLogoutRedirectUri: window.location.origin });
  }

  async function getAccessToken(): Promise<string> {
    if (!isAuthConfigured) return "";

    const account = instance.getActiveAccount() ?? accounts[0] ?? instance.getAllAccounts()[0];
    if (!account) return "";

    if (!instance.getActiveAccount()) {
      instance.setActiveAccount(account);
    }

    try {
      const response = await instance.acquireTokenSilent({
        ...loginRequest,
        account,
      });
      // When using a custom API scope, the access token is for our backend.
      // When using User.Read (default), the access token is for MS Graph,
      // so we use the ID token instead (audience = our client ID).
      return hasCustomApiScope ? response.accessToken : (response.idToken || "");
    } catch (err) {
      if (err instanceof InteractionRequiredAuthError) {
        await instance.acquireTokenRedirect(loginRequest);
        return "";
      }
      console.error("Token acquisition failed:", err);
      return "";
    }
  }

  return (
    <AuthContext.Provider
      value={{ isAuthenticated, isLoading, user, login, logout, getAccessToken }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function AppAuthProvider({ children }: { children: ReactNode }) {
  // When auth is not configured, skip MSAL entirely
  if (!isAuthConfigured) {
    return (
      <AuthContext.Provider
        value={{
          isAuthenticated: true,
          isLoading: false,
          user: null,
          login: async () => {},
          logout: () => {},
          getAccessToken: async () => "",
        }}
      >
        {children}
      </AuthContext.Provider>
    );
  }

  return (
    <MsalProvider instance={msalInstance}>
      <AuthContextProvider>{children}</AuthContextProvider>
    </MsalProvider>
  );
}
