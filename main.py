
import os
import io
import hashlib
from decimal import Decimal
from datetime import datetime

import psycopg2
from dotenv import load_dotenv
from ofxparse import OfxParser


load_dotenv()




import re
import io
from ofxparse import OfxParser


def ler_extrato(caminho):
    with open(caminho, "rb") as arquivo:
        conteudo = arquivo.read()

    # Decodifica o arquivo, mesmo se ele não estiver em UTF-8.
    try:
        texto = conteudo.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = conteudo.decode("cp1252")

    # Corrige declarações inválidas, como cpUTF-8 e cpNONE.
    texto = re.sub(
        r"(?im)^(\s*CHARSET\s*:\s*)(?:cp)?(?:UTF-8|NONE|1252|WINDOWS-1252)\s*$",
        r"\g<1>1252",
        texto
    )

    # Também corrige a declaração de codificação no cabeçalho XML,
    # caso exista.
    texto = re.sub(
        r'(?i)(encoding\s*=\s*["\'])[^"\']+(["\'])',
        r'\g<1>UTF-8\2',
        texto
    )

    arquivo_memoria = io.BytesIO(texto.encode("utf-8"))

    ofx = OfxParser.parse(arquivo_memoria)

    return ofx.account.statement.transactions



def gerar_chave(transacao):
    """Gera uma chave para evitar importações duplicadas."""

    identificador = getattr(transacao, "id", None)

    if identificador:
        conteudo = f"FITID:{identificador}"
    else:
        data = transacao.date.date() if isinstance(
            transacao.date, datetime
        ) else transacao.date

        conteudo = (
            f"{data}|{transacao.amount}|{transacao.memo or ''}"
        )

    return hashlib.sha256(
        conteudo.encode("utf-8")
    ).hexdigest()


def salvar_movimentacoes(transacoes):
    """Salva as movimentações no PostgreSQL do Neon."""

    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise ValueError(
            "A variável DATABASE_URL não foi configurada. "
            "Verifique o arquivo .env."
        )

    conexao = psycopg2.connect(database_url)

    try:
        with conexao:
            with conexao.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS movimentacoes (
                        id SERIAL PRIMARY KEY,
                        data_movimentacao DATE NOT NULL,
                        valor NUMERIC(15,2) NOT NULL,
                        descricao VARCHAR(255),
                        chave_transacao VARCHAR(64) UNIQUE
                    )
                """)

                # Compatibilidade com a tabela original da atividade.
                cursor.execute("""
                    ALTER TABLE movimentacoes
                    ADD COLUMN IF NOT EXISTS
                    chave_transacao VARCHAR(64)
                """)

                cursor.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS
                    idx_movimentacoes_chave_transacao
                    ON movimentacoes (chave_transacao)
                """)

                inseridas = 0
                duplicadas = 0

                for t in transacoes:
                    data = (
                        t.date.date()
                        if isinstance(t.date, datetime)
                        else t.date
                    )

                    valor = Decimal(str(t.amount))
                    descricao = (t.memo or "Sem descrição")[:255]
                    chave = gerar_chave(t)

                    cursor.execute("""
                        INSERT INTO movimentacoes (
                            data_movimentacao,
                            valor,
                            descricao,
                            chave_transacao
                        )
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (chave_transacao) DO NOTHING
                    """, (data, valor, descricao, chave))

                    if cursor.rowcount == 1:
                        inseridas += 1
                    else:
                        duplicadas += 1

        return inseridas, duplicadas

    finally:
        conexao.close()


def main():
    caminho = "extrato.ofx"

    if not os.path.exists(caminho):
        print(f"Erro: arquivo '{caminho}' não encontrado.")
        return

    try:
        transacoes = ler_extrato(caminho)

        if not transacoes:
            print("Nenhuma movimentação encontrada no extrato.")
            return

        entradas = Decimal("0.00")
        saidas = Decimal("0.00")

        print("\n========== EXTRATO BANCÁRIO ==========\n")

        for t in transacoes:
            valor = Decimal(str(t.amount))
            data = (
                t.date.date()
                if isinstance(t.date, datetime)
                else t.date
            )

            descricao = t.memo or "Sem descrição"

            print(f"Data      : {data.strftime('%d/%m/%Y')}")
            print(f"Valor     : R$ {valor:.2f}")
            print(f"Descrição : {descricao}")
            print("-" * 40)

            if valor > 0:
                entradas += valor
            elif valor < 0:
                saidas += valor

        saldo = entradas + saidas

        print("\n========== RESUMO FINANCEIRO ==========")
        print(f"Quantidade de movimentações: {len(transacoes)}")
        print(f"Total de entradas: R$ {entradas:.2f}")
        print(f"Total de saídas: R$ {abs(saidas):.2f}")
        print(f"Saldo calculado: R$ {saldo:.2f}")

        inseridas, duplicadas = salvar_movimentacoes(transacoes)

        print("\n========== IMPORTAÇÃO NO NEON ==========")
        print(f"Movimentações inseridas: {inseridas}")
        print(f"Movimentações duplicadas ignoradas: {duplicadas}")
        print("Processamento concluído!")

    except Exception as erro:
        print(f"Erro ao processar o extrato: {erro}")


if __name__ == "__main__":
    main()
