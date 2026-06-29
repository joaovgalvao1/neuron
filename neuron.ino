// ============================================================
// PLACAR NEURON — Arduino + BUZZER TEMPORIZADO
// 5 LEDs por time, controlados via Serial pelo Python
// ============================================================

// ====== PINOS ====== //
int led1Vermelho = 40, led2Vermelho = 41, led3Vermelho = 42, led4Vermelho = 43, led5Vermelho = 44;
int led1Azul     = 23, led2Azul     = 22, led3Azul     = 19, led4Azul     = 18, led5Azul     = 17;
int led1Amarelo  = 37, led2Amarelo  = 36, led3Amarelo  = 35, led4Amarelo  = 34, led5Amarelo  = 33;
int led1Verde    = 16, led2Verde    = 15, led3Verde    = 14, led4Verde    =  2, led5Verde    =  3;

int buzzer = 8;

// ====== CONTADORES (0 a 5) ====== //
int contVermelho = 0;
int contAzul     = 0;
int contAmarelo  = 0;
int contVerde    = 0;

// ====== ARRAYS ====== //
int ledsVermelho[5];
int ledsAzul[5];
int ledsAmarelo[5];
int ledsVerde[5];

// ====== VARIÁVEIS DO BUZZER ====== //
int padrao_sonoro;
unsigned long tempoAnteriorBuzzer = 0;
const unsigned long intervaloBuzzer = 300000; // 5 minutos em milissegundos (300000 ms)

// ============================================================
void setup() {
  Serial.begin(9600);

  // Configuração dos Arrays de LEDs
  ledsVermelho[0] = led1Vermelho; ledsVermelho[1] = led2Vermelho;
  ledsVermelho[2] = led3Vermelho; ledsVermelho[3] = led4Vermelho; ledsVermelho[4] = led5Vermelho;

  ledsAzul[0] = led1Azul; ledsAzul[1] = led2Azul;
  ledsAzul[2] = led3Azul; ledsAzul[3] = led4Azul; ledsAzul[4] = led5Azul;

  ledsAmarelo[0] = led1Amarelo; ledsAmarelo[1] = led2Amarelo;
  ledsAmarelo[2] = led3Amarelo; ledsAmarelo[3] = led4Amarelo; ledsAmarelo[4] = led5Amarelo;

  ledsVerde[0] = led1Verde; ledsVerde[1] = led2Verde;
  ledsVerde[2] = led3Verde; ledsVerde[3] = led4Verde; ledsVerde[4] = led5Verde;

  // Configuração dos Pinos como Saída
  for (int i = 0; i < 5; i++) {
    pinMode(ledsVermelho[i], OUTPUT);
    pinMode(ledsAzul[i],     OUTPUT);
    pinMode(ledsAmarelo[i],  OUTPUT);
    pinMode(ledsVerde[i],    OUTPUT);
  }

  pinMode(buzzer, OUTPUT);

  // Cria uma aleatoriedade real lendo um pino analógico vazio
  randomSeed(analogRead(0)); 

  // Executa o teste de inicialização dos LEDs
  testarTodos();
  
  // Inicializa o cronômetro do buzzer após o setup
  tempoAnteriorBuzzer = millis();
}

// ============================================================
void loop() {
  // 1. VERIFICAÇÃO DO PLACAR (SERIAL)
  if (Serial.available() > 0) {
    char cmd = Serial.read();

    processarComando(cmd);
  }

  // 2. VERIFICAÇÃO DO BUZZER (TEMPO)
  // Verifica se já se passaram 5 minutos (300.000 ms) sem travar o loop
  if (millis() - tempoAnteriorBuzzer >= intervaloBuzzer) {
    tocarBuzzerAleatorio();
    tempoAnteriorBuzzer = millis(); // Reinicia o cronômetro para os próximos 5 minutos
  }
}

// ============================================================
// Lógica de sorteio e execução dos padrões do buzzer
// ============================================================
void tocarBuzzerAleatorio() {
  padrao_sonoro = random(1, 3); // Sorteia 1 ou 2
  
  if (padrao_sonoro == 1) {
    // PADRÃO 1: Intervalado
    for (int i = 0; i < 5; i++) {
      digitalWrite(buzzer, HIGH);
      delay(200);
      digitalWrite(buzzer, LOW);
      delay(1800);
    }
  } 
  else {
    // PADRÃO 2: Contínuo (Rápido)
    for (int i = 0; i < 75; i++) {
      digitalWrite(buzzer, HIGH);
      delay(100);
      digitalWrite(buzzer, LOW);
      delay(100);
    }
  }
}

// ============================================================
void processarComando(char cmd) {
  switch (cmd) {
    // +1 ponto
    case 'R': alterarPontos(contVermelho, ledsVermelho,  1); break;
    case 'G': alterarPontos(contVerde,    ledsVerde,     1); break;
    case 'B': alterarPontos(contAzul,     ledsAzul,      1); break;
    case 'Y': alterarPontos(contAmarelo,  ledsAmarelo,   1); break;

    // +2 pontos
    case 'Q': alterarPontos(contVermelho, ledsVermelho,  2); break;
    case 'H': alterarPontos(contVerde,    ledsVerde,     2); break;
    case 'C': alterarPontos(contAzul,     ledsAzul,      2); break;
    case 'Z': alterarPontos(contAmarelo,  ledsAmarelo,   2); break;

    // -1 ponto
    case 'r': alterarPontos(contVermelho, ledsVermelho, -1); break;
    case 'g': alterarPontos(contVerde,    ledsVerde,    -1); break;
    case 'b': alterarPontos(contAzul,     ledsAzul,     -1); break;
    case 'y': alterarPontos(contAmarelo,  ledsAmarelo,  -1); break;

    // Zerar time
    case 'w': zerarTime(contVermelho, ledsVermelho); break;
    case 'h': zerarTime(contVerde,    ledsVerde);    break;
    case 'c': zerarTime(contAzul,     ledsAzul);     break;
    case 'z': zerarTime(contAmarelo,  ledsAmarelo);  break;

    // Zerar tudo
    case 'X':
      zerarTime(contVermelho, ledsVermelho);
      zerarTime(contVerde,    ledsVerde);
      zerarTime(contAzul,     ledsAzul);
      zerarTime(contAmarelo,  ledsAmarelo);
      break;
  }
}

// ============================================================
void alterarPontos(int &cont, int leds[], int delta) {
  cont += delta;
  if (cont < 0) cont = 0;
  if (cont > 5) cont = 5;
  atualizarLEDs(leds, cont);
  imprimirPlacar();
}

void zerarTime(int &cont, int leds[]) {
  cont = 0;
  atualizarLEDs(leds, 0);
  imprimirPlacar();
}

// ============================================================
void atualizarLEDs(int leds[], int quantidade) {
  for (int i = 0; i < 5; i++) {
    if (i < quantidade) {
      digitalWrite(leds[i], HIGH);
    } else {
      digitalWrite(leds[i], LOW);
    }
  }
}

// ============================================================
void imprimirPlacar() {
  Serial.print(" Vermelho:");
  Serial.print(contVermelho);
  Serial.print(" Verde:");
  Serial.print(contVerde);
  Serial.print(" Azul:");
  Serial.print(contAzul);
  Serial.print(" Amarelo:");
  Serial.println(contAmarelo);
}

// ============================================================
void testarTodos() {
  for (int i = 0; i < 5; i++) {
    digitalWrite(ledsVermelho[i], HIGH);
    digitalWrite(ledsAzul[i],     HIGH);
    digitalWrite(ledsAmarelo[i],  HIGH);
    digitalWrite(ledsVerde[i],    HIGH);
  }
  delay(1000);
  for (int i = 0; i < 5; i++) {
    digitalWrite(ledsVermelho[i], LOW);
    digitalWrite(ledsAzul[i],     LOW);
    digitalWrite(ledsAmarelo[i],  LOW);
    digitalWrite(ledsVerde[i],    LOW);
  }
}
// ============================================================
void sortearHost() {
  
}